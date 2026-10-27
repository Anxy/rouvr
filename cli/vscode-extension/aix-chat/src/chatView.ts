/*---------------------------------------------------------------------------------------------
 *  AIX Engineering Workspace — Next-Generation sidebar panel.
 *
 *  Layout (top → bottom):
 *    Header     40px   — logo · workspace · connection · model · settings
 *    History         — slide-over drawer (absolute overlay)
 *    Conversation    — flex-1, document-style, tool timelines, code blocks, diffs
 *    Context bar     — removable chips (hidden when empty)
 *    Composer        — mode pills · slash menu · textarea · attach · send
 *    Footer     22px  — model · latency · tokens
 *--------------------------------------------------------------------------------------------*/
import * as fs from 'fs/promises';
import * as path from 'path';
import * as vscode from 'vscode';
import { AixAuthManager } from './auth';
import { AixEditSession, FileChangeSummary } from './editSession';
import { classifyAgentIntent, isPureGreeting } from './intent';
import { redactSecrets } from './secrets';
import {
	CycleEvent,
	CycleItem,
	DiscussMessage,
	DiscussRequestOptions,
	AWAITING_USER_PHASES,
	TERMINAL_CYCLE_PHASES,
	admitErrorCode,
	getCycle,
	getCycleDiff,
	getCycleEvents,
	getCyclePlan,
	getCyclePreview,
	isObjectiveTerminal,
	isStoredActiveRunFinished,
	objectiveOutcome,
	objectiveOutcomeLabel,
	planStepsFromExecutionStages,
	postAdmit,
	postCycleAction,
	postDiscuss,
	postStartCycle,
	settlePlanForOutcome,
} from './discussApi';

/* ── Types ──────────────────────────────────────────────────────────────────── */

interface ConversationSummary {
	id: string;
	title: string;
	preview: string;
	timestamp: number;
	pinned?: boolean;
}

interface PlanStep {
	id: string;
	label: string;
	status: 'done' | 'running' | 'todo' | 'error';
}

interface ActiveTaskState {
	conversationId: string;
	activeObjectiveId?: string;
	activeCycleId?: string;
	workhubTaskId?: string;
	currentState: string;
	currentStep?: string;
	plan: PlanStep[];
	changedFiles: string[];
	testResults?: { passed?: number; failed?: number; summary?: string };
	pendingApproval?: boolean;
	lastClientMessageId?: string;
	objectiveSummary?: string;
	/** Backend task_family / binding ids when known (Sprint 002+). */
	taskFamily?: string;
	objectiveSourceMessageId?: string;
	reasonCode?: string;
}

interface StoredConversation extends ConversationSummary {
	messages: DiscussMessage[];
	threadId?: string;
	mode: string;
	activeTask?: ActiveTaskState;
}

interface StructuredError {
	title: string;
	status?: number;
	correlationId?: string;
	causes?: string[];
	actions?: string[];
	raw?: string;
}

type WebviewMsg =
	| { type: 'ready' }
	| { type: 'signIn' }
	| { type: 'send'; text: string; mode: string }
	| { type: 'newChat' }
	| { type: 'loadConversation'; id: string }
	| { type: 'attach' }
	| { type: 'removeAttachment'; index: number }
	| { type: 'quickAction'; action: string }
	| { type: 'deleteConversation'; id: string }
	| { type: 'pinConversation'; id: string; pinned: boolean }
	| { type: 'searchHistory'; query: string }
	| { type: 'runAction'; action: string; data?: unknown }
	| { type: 'admitAndRun'; objective?: string; taskClass?: string; proposedScope?: unknown }
	| { type: 'cancel' }
	| { type: 'openLocalFile'; filePath: string }
	| { type: 'keepEdit'; filePath?: string }
	| { type: 'undoEdit'; filePath?: string }
	| { type: 'openDiff'; filePath?: string }
	| { type: 'runControl'; action: 'pause' | 'resume' | 'cancel' | 'approve' | 'reject' | 'diff' | 'tests' };

type ExtMsg =
	| { type: 'authState'; signedIn: boolean; username?: string; tenantId?: string; workspace?: string }
	| { type: 'thinking'; value: boolean; stage?: string }
	| { type: 'toolCall'; id: string; name: string; status: 'running' | 'done' | 'error'; detail?: string }
	| { type: 'assistantMessage'; text: string; error?: StructuredError }
	| { type: 'displayUserMessage'; text: string }
	| { type: 'attachmentsUpdated'; attachments: { filename: string }[] }
	| { type: 'historyUpdated'; items: ConversationSummary[] }
	| { type: 'conversationLoaded'; messages: { role: string; text: string }[]; mode: string }
	| { type: 'statsUpdated'; model: string; latencyMs: number; contextTokens: number }
	| { type: 'configError'; message: string }
	| { type: 'fileChanges'; changes: FileChangeSummary[]; activePath?: string }
	| { type: 'activeRun'; task: ActiveTaskState | null };

interface PendingAttachment {
	local_path: string;
	filename: string;
	mime: string;
}

/* ── Provider ───────────────────────────────────────────────────────────────── */

export class AixChatViewProvider implements vscode.WebviewViewProvider {
	public static readonly viewType = 'aix.chatView';

	private view: vscode.WebviewView | undefined;
	private currentConv: StoredConversation | undefined;
	private pendingAttachments: PendingAttachment[] = [];
	private activeAbortController: AbortController | undefined;
	private sendStart = 0;
	private totalTokens = 0;
	/** Survives webview reloads / lost currentConv pointers. */
	private lastActiveTask: ActiveTaskState | undefined;
	private readonly edits: AixEditSession;
	/**
	 * Cycle IDs with an in-flight `watchCycle` poll loop. `watchCycle` is called from
	 * two places (`handleAdmitAndRun` once at creation, `handleRunControl`'s
	 * resume/approve branch on any later Approve/Resume action) with no coordination
	 * between them — without this guard, a second call for the same cycle starts a
	 * fully independent loop with its own `shownFiles`/`approvalAttempted` dedup state,
	 * which re-discovers and re-presents everything the first loop already showed and
	 * re-fires `approveAndContinue` a second time. This is exactly that fix.
	 */
	private readonly watchingCycles = new Set<string>();

	constructor(
		private readonly extensionUri: vscode.Uri,
		private readonly auth: AixAuthManager,
		private readonly context: vscode.ExtensionContext,
	) {
		this.edits = new AixEditSession(context);
		context.subscriptions.push(this.edits);
		context.subscriptions.push(this.edits.onDidChange((changes, activePath) => {
			this.post({ type: 'fileChanges', changes, activePath });
		}));
	}

	private webviewReady = false;
	private readyCheckTimer: ReturnType<typeof setTimeout> | undefined;
	private webviewLoadAttempts = 0;
	private static readonly READY_TIMEOUT_MS = 8_000;
	private static readonly MAX_WEBVIEW_RETRIES = 3;

	resolveWebviewView(webviewView: vscode.WebviewView): void {
		this.view = webviewView;
		webviewView.webview.options = {
			enableScripts: true,
			localResourceRoots: [this.extensionUri],
		};
		this.mountWebview(webviewView);

		this.auth.onDidChangeAuthState(() => this.postAuthState(), null, this.context.subscriptions);

		webviewView.onDidDispose(() => {
			this.cancelReadyCheck();
			if (this.view === webviewView) {
				this.view = undefined;
			}
		});

		webviewView.webview.onDidReceiveMessage(async (msg: WebviewMsg) => {
			switch (msg.type) {
				case 'ready':
					this.webviewReady = true;
					this.cancelReadyCheck();
					await this.onReady();
					break;
				case 'signIn':              await this.auth.signIn(); await this.postAuthState(); break;
				case 'newChat':
					this.currentConv = undefined;
					this.lastActiveTask = undefined;
					this.postActiveRun(null);
					break;
				case 'send':                await this.handleSend(msg.text, msg.mode); break;
				case 'attach':              await this.handleAttach(); break;
				case 'removeAttachment':    this.pendingAttachments.splice(msg.index, 1); this.postAttachments(); break;
				case 'loadConversation':    await this.loadConv(msg.id); break;
				case 'deleteConversation':  await this.deleteConv(msg.id); break;
				case 'pinConversation':     await this.pinConv(msg.id, msg.pinned); break;
				case 'quickAction':         await this.handleQuickAction(msg.action); break;
				case 'searchHistory':       await this.postHistory(msg.query || undefined); break;
				case 'runAction':           await this.handleRunAction(msg.action, msg.data); break;
				case 'admitAndRun':         await this.handleAdmitAndRun(msg.objective, msg.taskClass, msg.proposedScope); break;
				case 'cancel':              this.handleCancel(); break;
				case 'openLocalFile':       await this.openLocalFile(msg.filePath); break;
				case 'keepEdit':
					if (msg.filePath) await this.edits.keep(msg.filePath);
					else await this.edits.keepAll();
					break;
				case 'undoEdit':
					if (msg.filePath) await this.edits.undo(msg.filePath);
					else await this.edits.undoAll();
					break;
				case 'openDiff':
					if (msg.filePath) await this.edits.openDiff(msg.filePath);
					else await vscode.commands.executeCommand('aix.showChanges');
					break;
				case 'runControl':
					await this.handleRunControl(msg.action);
					break;
			}
		});
	}

	/**
	 * Assign HTML and watch for the webview's `ready` ping.
	 * VS Code occasionally fails its internal service-worker registration
	 * (InvalidStateError / "document is in an invalid state"); re-assigning
	 * html forces a fresh iframe + SW attempt. See microsoft/vscode#125993.
	 */
	private mountWebview(webviewView: vscode.WebviewView): void {
		this.webviewReady = false;
		this.webviewLoadAttempts = 0;
		webviewView.webview.html = this.renderHtml();
		this.scheduleReadyCheck(webviewView);
	}

	private scheduleReadyCheck(webviewView: vscode.WebviewView): void {
		this.cancelReadyCheck();
		this.readyCheckTimer = setTimeout(() => {
			void this.retryWebviewIfNeeded(webviewView);
		}, AixChatViewProvider.READY_TIMEOUT_MS);
	}

	private cancelReadyCheck(): void {
		if (this.readyCheckTimer !== undefined) {
			clearTimeout(this.readyCheckTimer);
			this.readyCheckTimer = undefined;
		}
	}

	private async retryWebviewIfNeeded(webviewView: vscode.WebviewView): Promise<void> {
		if (this.webviewReady || this.view !== webviewView) {
			return;
		}
		this.webviewLoadAttempts += 1;
		if (this.webviewLoadAttempts > AixChatViewProvider.MAX_WEBVIEW_RETRIES) {
			const pick = await vscode.window.showWarningMessage(
				'AIX chat webview failed to load (VS Code service worker). Reload the window to fix.',
				'Reload Window',
			);
			if (pick === 'Reload Window') {
				await vscode.commands.executeCommand('workbench.action.reloadWindow');
			}
			return;
		}
		// Re-assigning html recreates the iframe and retries SW registration.
		webviewView.webview.html = this.renderHtml();
		this.scheduleReadyCheck(webviewView);
	}

	private post(m: ExtMsg): void { void this.view?.webview.postMessage(m); }

	private async onReady(): Promise<void> {
		await this.auth.whenReady();
		await this.postAuthState();
		await this.postHistory();
		this.post({ type: 'statsUpdated', model: this.modelName(), latencyMs: 0, contextTokens: 0 });
		this.post({ type: 'fileChanges', changes: this.edits.summaries() });
		await this.restoreActiveRunOnOpen();
	}

	/**
	 * History keeps the last activeTask forever — without this, opening the AIX
	 * panel resurrects a COMPLETED run with a stuck ● "Execute cycle" chip and
	 * looks like work is still in progress. Finished / gone cycles are dismissed;
	 * only truly live runs stay in the Active Run bar.
	 */
	private async restoreActiveRunOnOpen(): Promise<void> {
		const recovered = this.resolveActiveTask();
		if (!recovered?.activeCycleId) {
			this.postActiveRun(null);
			return;
		}

		if (isStoredActiveRunFinished(recovered.currentState)) {
			await this.dismissFinishedActiveRun(recovered);
			return;
		}

		// Probe backend: after service restart the cycle is gone; treat as finished.
		try {
			const config = vscode.workspace.getConfiguration('aix');
			const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
			const accessToken = await this.auth.getAccessToken();
			const tenantId = await this.auth.getTenantId();
			if (accessToken) {
				const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, recovered.activeCycleId);
				if (this.isCycleGone(statusRes.json as Record<string, unknown>, statusRes.status)) {
					await this.dismissFinishedActiveRun(recovered);
					return;
				}
				if (statusRes.status < 400) {
					const item = (statusRes.json.item || statusRes.json) as CycleItem;
					const phase = String(item?.phase || item?.status?.phase || recovered.currentState || '').toUpperCase();
					recovered.currentState = phase;
					if (item?.execution_stages?.length) {
						recovered.plan = planStepsFromExecutionStages(item.execution_stages);
					}
					recovered.plan = settlePlanForOutcome(recovered.plan || [], objectiveOutcome(item));
					if (isObjectiveTerminal(item) || isStoredActiveRunFinished(phase)) {
						await this.dismissFinishedActiveRun(recovered);
						return;
					}
					await this.persistActiveTask(recovered);
					return;
				}
			}
		} catch {
			// Offline / auth — fall through to local heuristics only.
		}

		// Local-only: settle stuck ● chips so a half-updated COMPLETED row never shows.
		if (recovered.plan?.some(s => s.status === 'running') && isStoredActiveRunFinished(recovered.currentState)) {
			await this.dismissFinishedActiveRun(recovered);
			return;
		}
		this.postActiveRun(recovered);
	}

	/** Strip finished activeTask from history and hide the Active Run bar (no chat spam). */
	private async dismissFinishedActiveRun(task: ActiveTaskState): Promise<void> {
		const cycleId = task.activeCycleId;
		this.lastActiveTask = undefined;
		if (this.currentConv?.activeTask?.activeCycleId === cycleId) {
			this.currentConv.activeTask = undefined;
		}
		const history = this.getHistory().map(h => {
			if (cycleId && h.activeTask?.activeCycleId === cycleId) {
				return { ...h, activeTask: undefined };
			}
			if (this.currentConv && h.id === this.currentConv.id) {
				return { ...h, activeTask: undefined };
			}
			return h;
		});
		await this.saveHistory(history);
		this.postActiveRun(null);
	}

	private modelName(): string {
		return vscode.workspace.getConfiguration('aix').get<string>('model', '') || 'aix';
	}

	private async postAuthState(): Promise<void> {
		const username = await this.auth.getUsername();
		const tenantId = await this.auth.getTenantId();
		const workspace = vscode.workspace.workspaceFolders?.[0]?.name ?? '';
		this.post({ type: 'authState', signedIn: Boolean(username), username, tenantId, workspace });
	}

	private postAttachments(): void {
		this.post({ type: 'attachmentsUpdated', attachments: this.pendingAttachments.map(a => ({ filename: a.filename })) });
	}

	/* ── History persistence ──────────────────────────────────────────────────── */

	private getHistory(): StoredConversation[] {
		return this.context.globalState.get<StoredConversation[]>('aix.history', []);
	}

	private async saveHistory(h: StoredConversation[]): Promise<void> {
		await this.context.globalState.update('aix.history', h.slice(-50));
	}

	private async postHistory(filter?: string): Promise<void> {
		let list = this.getHistory();
		if (filter) {
			const q = filter.toLowerCase();
			list = list.filter(h => h.title.toLowerCase().includes(q) || h.preview.toLowerCase().includes(q));
		}
		list.sort((a, b) => (a.pinned !== b.pinned ? (a.pinned ? -1 : 1) : b.timestamp - a.timestamp));
		this.post({ type: 'historyUpdated', items: list.map(({ id, title, preview, timestamp, pinned }) => ({ id, title, preview, timestamp, pinned })) });
	}

	private async loadConv(id: string): Promise<void> {
		const conv = this.getHistory().find(h => h.id === id);
		if (!conv) return;
		this.currentConv = conv;
		this.totalTokens = 0;
		this.post({ type: 'conversationLoaded', messages: conv.messages.map(m => ({ role: m.role, text: m.content })), mode: conv.mode || 'chat' });
		const task = conv.activeTask;
		if (task && isStoredActiveRunFinished(task.currentState)) {
			await this.dismissFinishedActiveRun(task);
			return;
		}
		this.postActiveRun(task);
	}

	private async deleteConv(id: string): Promise<void> {
		await this.saveHistory(this.getHistory().filter(h => h.id !== id));
		if (this.currentConv?.id === id) this.currentConv = undefined;
		await this.postHistory();
	}

	private async pinConv(id: string, pinned: boolean): Promise<void> {
		const list = this.getHistory();
		const conv = list.find(h => h.id === id);
		if (conv) { conv.pinned = pinned; await this.saveHistory(list); }
		await this.postHistory();
	}

	/* ── Actions ──────────────────────────────────────────────────────────────── */

	private async handleAttach(): Promise<void> {
		const picked = await vscode.window.showOpenDialog({ canSelectMany: true, openLabel: 'Attach to AIX' });
		if (!picked?.length) return;
		for (const uri of picked) {
			const filename = path.basename(uri.fsPath);
			this.pendingAttachments.push({ local_path: uri.fsPath, filename, mime: guessMime(filename) });
		}
		this.postAttachments();
	}

	private async handleQuickAction(action: string): Promise<void> {
		const MAP: Record<string, { text: string; mode: string }> = {
			explain:      { text: '/explain Explain the selected code in detail', mode: 'chat' },
			fix:          { text: '/fix Identify and fix the error in the current file', mode: 'debug' },
			review:       { text: '/review Review this code for bugs, security issues, and improvements', mode: 'review' },
			test:         { text: '/test Generate comprehensive unit tests for the selected code', mode: 'generate' },
			security:     { text: '/security Perform a full security audit of this codebase', mode: 'review' },
			architecture: { text: '/architect Describe the system architecture and suggest improvements', mode: 'architect' },
			docs:         { text: '/doc Generate documentation for the selected code', mode: 'generate' },
			optimize:     { text: '/perf Identify performance bottlenecks and suggest optimizations', mode: 'review' },
		};
		const entry = MAP[action];
		if (!entry) return;
		this.post({ type: 'displayUserMessage', text: entry.text });
		await this.handleSend(entry.text, entry.mode);
	}

	private async handleRunAction(action: string, data?: unknown): Promise<void> {
		if (action === 'openSettings') {
			await vscode.commands.executeCommand('workbench.action.openSettings', 'aix.');
			return;
		}
		if (action === 'openLocalFile' && typeof data === 'string') {
			await this.openLocalFile(data);
		}
	}

	private async openLocalFile(filePath: string): Promise<void> {
		if (!filePath) return;
		try {
			const uri = vscode.Uri.file(filePath);
			await vscode.window.showTextDocument(uri, { preview: false });
		} catch (err) {
			void vscode.window.showErrorMessage(`Could not open ${filePath}: ${err instanceof Error ? err.message : String(err)}`);
		}
	}

	private handleCancel(): void {
		if (this.activeAbortController) {
			this.activeAbortController.abort();
			this.activeAbortController = undefined;
		}
		this.post({ type: 'thinking', value: false });
		this.post({ type: 'toolCall', id: 'cancel', name: 'Cancelled by user', status: 'error' });
		this.post({ type: 'assistantMessage', text: '\u23F9 Execution stopped by user.' });
	}

	private postActiveRun(task: ActiveTaskState | null | undefined): void {
		this.post({ type: 'activeRun', task: task || null });
	}

	/** Drop a local active-run that no longer exists on the AIX service (restart / in-memory loss). */
	private async clearStaleActiveTask(reason?: string): Promise<void> {
		const goneId = this.currentConv?.activeTask?.activeCycleId || this.lastActiveTask?.activeCycleId;
		this.lastActiveTask = undefined;
		if (this.currentConv) {
			this.currentConv.activeTask = undefined;
		}
		const history = this.getHistory().map(h => {
			if (goneId && h.activeTask?.activeCycleId === goneId) {
				return { ...h, activeTask: undefined };
			}
			if (this.currentConv && h.id === this.currentConv.id) {
				return { ...h, activeTask: undefined };
			}
			return h;
		});
		await this.saveHistory(history);
		this.postActiveRun(null);
		if (reason) {
			this.post({ type: 'assistantMessage', text: reason });
		}
	}

	private isCycleGone(json: Record<string, unknown> | undefined, status?: number): boolean {
		if (status === 404) return true;
		if (!json) return false;
		const err = json.error;
		if (err && typeof err === 'object') {
			const code = String((err as { code?: string }).code || '').toUpperCase();
			if (code === 'CYCLE_NOT_FOUND' || code === 'OBJECTIVE_NOT_FOUND') return true;
		}
		const blob = formatCycleError(json).toUpperCase();
		return blob.includes('CYCLE_NOT_FOUND') || blob.includes('OBJECTIVE_NOT_FOUND');
	}

	private resolveActiveTask(): ActiveTaskState | undefined {
		if (this.currentConv?.activeTask?.activeCycleId) {
			this.lastActiveTask = this.currentConv.activeTask;
			return this.currentConv.activeTask;
		}
		if (this.lastActiveTask?.activeCycleId) {
			if (this.currentConv) this.currentConv.activeTask = this.lastActiveTask;
			return this.lastActiveTask;
		}
		// Recover newest active task from history
		const hist = this.getHistory();
		for (let i = hist.length - 1; i >= 0; i--) {
			const t = hist[i].activeTask;
			if (t?.activeCycleId) {
				this.lastActiveTask = t;
				if (!this.currentConv) this.currentConv = hist[i];
				else this.currentConv.activeTask = t;
				return t;
			}
		}
		return undefined;
	}

	private async persistActiveTask(task: ActiveTaskState): Promise<void> {
		this.lastActiveTask = task;
		if (!this.currentConv) {
			this.currentConv = {
				id: task.conversationId || uid(),
				title: trunc(task.objectiveSummary || 'AIX run', 55),
				preview: trunc(task.objectiveSummary || '', 80),
				timestamp: Date.now(),
				messages: [],
				mode: 'agent',
				activeTask: task,
			};
		} else {
			this.currentConv.activeTask = task;
		}
		const history = this.getHistory().filter(h => h.id !== this.currentConv!.id);
		history.push(this.currentConv);
		await this.saveHistory(history);
		this.postActiveRun(task);
	}

	private async retryBlockedCycle(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		task: ActiveTaskState,
	): Promise<boolean> {
		const cycleId = task.activeCycleId!;
		this.post({ type: 'toolCall', id: 'retry', name: 'Retrying blocked cycle', status: 'running' });

		let cycleVersion: number | string | undefined;
		try {
			const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, cycleId);
			const item = (statusRes.json.item || statusRes.json) as CycleItem;
			cycleVersion = item?.version;
			if (item?.phase) task.currentState = String(item.phase);
		} catch { /* backend returns 428 without If-Match */ }

		const res = await postCycleAction(
			orchestratorUrl, accessToken, tenantId, cycleId, 'retry-step',
			undefined, `retry-${cycleId}-${Date.now()}`, { reason: 'studio-resume-blocked' }, cycleVersion,
		);
		let finalRes = res;
		if (res.status === 428 || (res.json.error as { code?: string } | undefined)?.code === 'VERSION_CONFLICT') {
			try {
				const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, cycleId);
				const item = (statusRes.json.item || statusRes.json) as CycleItem;
				cycleVersion = item?.version ?? cycleVersion;
			} catch { /* keep prior */ }
			finalRes = await postCycleAction(
				orchestratorUrl, accessToken, tenantId, cycleId, 'retry-step',
				undefined, `retry-${cycleId}-retry`, { reason: 'studio-resume-blocked' }, cycleVersion,
			);
		}
		if (finalRes.status >= 400 || finalRes.json.ok === false) {
			const err = finalRes.json.error as { code?: string; reason_codes?: string[] } | undefined;
			this.post({ type: 'toolCall', id: 'retry', name: 'Retrying blocked cycle', status: 'error', detail: err?.code || `HTTP ${finalRes.status}` });
			this.post({
				type: 'assistantMessage',
				text: `Could not retry blocked cycle (\`${err?.code || finalRes.status}\`). ${formatCycleError(finalRes.json)}`,
			});
			return false;
		}

		const phase = String(finalRes.json.phase || 'RUNNING');
		task.currentState = phase;
		await this.persistActiveTask(task);
		this.post({ type: 'toolCall', id: 'retry', name: 'Retrying blocked cycle', status: 'done', detail: phase });
		return true;
	}

	private async handleRunControl(action: 'pause' | 'resume' | 'cancel' | 'approve' | 'reject' | 'diff' | 'tests'): Promise<void> {
		const task = this.resolveActiveTask();
		if (!task?.activeCycleId && action !== 'diff' && action !== 'tests') {
			this.post({ type: 'assistantMessage', text: 'No active run to control. Start work with Agent mode first.' });
			return;
		}
		if (action === 'diff') {
			await this.replyWithDiffStatus();
			return;
		}
		if (action === 'tests') {
			await this.replyWithTestStatus();
			return;
		}
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();
		if (!accessToken || !task?.activeCycleId) return;

		this.post({ type: 'thinking', value: true, stage: `${action} run\u2026` });
		try {
			if (action === 'resume' || action === 'approve') {
				const phase = String(task.currentState || '').toUpperCase();
				// BLOCKED cycles need retry-step — resume/approval only unblocks WAITING_APPROVAL.
				const ok = phase === 'BLOCKED'
					? await this.retryBlockedCycle(orchestratorUrl, accessToken, tenantId, task)
					: await this.approveAndContinue(orchestratorUrl, accessToken, tenantId, task);
				if (ok) {
					this.post({
						type: 'assistantMessage',
						text: phase === 'BLOCKED'
							? 'Retry accepted — re-running the blocked cycle and applying files to your workspace\u2026'
							: 'Approval accepted — continuing execution and applying files to your workspace\u2026',
					});
					this.activeAbortController = new AbortController();
					await this.watchCycle(orchestratorUrl, accessToken, tenantId, task.activeCycleId!, this.activeAbortController.signal);
				}
				return;
			}

			const map: Record<string, string> = {
				pause: 'block',
				cancel: 'cancel',
				reject: 'reject',
			};
			const clientMessageId = `ctrl-${action}-${task.activeCycleId}-${Date.now()}`;
			let cycleVersion: number | string | undefined;
			try {
				const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, task.activeCycleId);
				const item = (statusRes.json.item || statusRes.json) as CycleItem;
				cycleVersion = item?.version;
			} catch { /* backend returns 428 without If-Match */ }
			const res = await postCycleAction(
				orchestratorUrl, accessToken, tenantId, task.activeCycleId, map[action],
				undefined, clientMessageId, undefined, cycleVersion,
			);
			if (this.isCycleGone(res.json, res.status)) {
				await this.clearStaleActiveTask(
					`The previous cycle (\`${task.activeCycleId}\`) is gone — usually after an AIX service restart.\n\n` +
					`Active run cleared. Send your next request in **Agent** mode to start a fresh objective.`,
				);
				return;
			}
			const ok = res.status < 400 && res.json.ok !== false;
			task.currentState = action === 'pause' ? 'PAUSED' : action === 'cancel' ? 'CANCELLED' : task.currentState;
			await this.persistActiveTask(task);
			this.post({
				type: 'assistantMessage',
				text: ok
					? `Run **${action}** accepted.`
					: `Could not ${action} run (HTTP ${res.status}). ${formatCycleError(res.json)}`,
			});
			if (action === 'cancel' && ok) {
				await this.clearStaleActiveTask();
			}
		} finally {
			this.post({ type: 'thinking', value: false });
			this.activeAbortController = undefined;
		}
	}

	/**
	 * WAITING_APPROVAL → attach local approval ref + resume so real_execute can run.
	 *
	 * `approval_request_id` is derived from the cycle id + the phase we observed it in
	 * (stable across retries within that phase, unlike the old `Date.now()`-based
	 * idempotency key) so a repeated call for the *same* approval collapses server-side
	 * instead of re-triggering `_maybe_real_execute` a second time.
	 */
	private async approveAndContinue(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		task: ActiveTaskState,
	): Promise<boolean> {
		const cycleId = task.activeCycleId!;
		const approvalRequestId = `${cycleId}:${task.currentState || 'unknown'}`;

		this.post({ type: 'toolCall', id: 'approve', name: 'Approving cycle to continue', status: 'running' });

		// Cycle control requires If-Match = current version (428 without it).
		// Also read approval identity (plan_id / plan_revision / approval_gate_id)
		// so resume proves which gate it authorizes — not just "whatever is current".
		let cycleVersion: number | string | undefined;
		let approvalBody: Record<string, unknown> = {
			approval_reference: 'workhub://approvals/aix-studio-local',
			approval_request_id: approvalRequestId,
		};
		try {
			const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, cycleId);
			const item = (statusRes.json.item || statusRes.json) as CycleItem;
			cycleVersion = item?.version;
			if (item?.phase) task.currentState = String(item.phase);
			if (item?.approval_gate_id) approvalBody.approval_gate_id = item.approval_gate_id;
			if (item?.plan_revision != null) approvalBody.plan_revision = item.plan_revision;
			if (item?.plan_id) approvalBody.plan_id = item.plan_id;
		} catch { /* proceed; backend will 428 if version missing */ }

		// Resume alone unblocks WAITING_APPROVAL (signals approval + continues).
		// Avoid a separate approval-reference call — older clients/BFF gaps 422'd it
		// when extra body fields were present, and it is redundant with resume.
		let res = await postCycleAction(
			orchestratorUrl, accessToken, tenantId, cycleId, 'resume',
			undefined, `resume-${approvalRequestId}`, approvalBody, cycleVersion,
		);
		if (res.status === 428 || (res.json.error as { code?: string } | undefined)?.code === 'VERSION_CONFLICT') {
			try {
				const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, cycleId);
				const item = (statusRes.json.item || statusRes.json) as CycleItem;
				cycleVersion = item?.version ?? cycleVersion;
				if (item?.approval_gate_id) approvalBody.approval_gate_id = item.approval_gate_id;
				if (item?.plan_revision != null) approvalBody.plan_revision = item.plan_revision;
				if (item?.plan_id) approvalBody.plan_id = item.plan_id;
			} catch { /* keep prior */ }
			res = await postCycleAction(
				orchestratorUrl, accessToken, tenantId, cycleId, 'resume',
				undefined, `resume-${approvalRequestId}-retry`, approvalBody, cycleVersion,
			);
		}

		if (res.status >= 400 || res.json.ok === false) {
			const err = res.json.error as { code?: string; current_phase?: string } | undefined;
			if (err?.code === 'VERSION_CONFLICT' && err.current_phase && err.current_phase !== 'WAITING_APPROVAL' && err.current_phase !== 'AWAITING_APPROVAL') {
				// The cycle already advanced past the phase we were trying to unblock —
				// nothing left to approve, treat as success instead of aborting the watch
				// loop or spinning a retry against a phase that no longer exists.
				task.currentState = err.current_phase;
				task.pendingApproval = false;
				await this.persistActiveTask(task);
				this.post({ type: 'toolCall', id: 'approve', name: 'Approving cycle to continue', status: 'done', detail: `already ${err.current_phase}` });
				return true;
			}
			if (this.isCycleGone(res.json, res.status)) {
				this.post({ type: 'toolCall', id: 'approve', name: 'Approving cycle to continue', status: 'error', detail: 'CYCLE_NOT_FOUND' });
				await this.clearStaleActiveTask(
					`Could not leave WAITING_APPROVAL — cycle \`${cycleId}\` no longer exists (AIX service likely restarted).\n\n` +
					`Active run cleared. Describe your task again in **Agent** mode to start a new objective.`,
				);
				return false;
			}
			this.post({ type: 'toolCall', id: 'approve', name: 'Approving cycle to continue', status: 'error', detail: `HTTP ${res.status}` });
			this.post({
				type: 'assistantMessage',
				text: `Could not leave WAITING_APPROVAL (HTTP ${res.status}). ${formatCycleError(res.json)}\n\nIf the AIX service restarted, start a new Agent run — in-memory cycle engines do not survive restarts.`,
			});
			return false;
		}

		const phase = String(res.json.phase || 'RUNNING');
		task.currentState = phase;
		task.pendingApproval = false;
		await this.persistActiveTask(task);
		this.post({ type: 'toolCall', id: 'approve', name: 'Approving cycle to continue', status: 'done', detail: phase });
		return true;
	}

	private async replyWithStatusQuery(): Promise<void> {
		const task = this.resolveActiveTask();
		if (!task?.activeCycleId) {
			this.post({ type: 'assistantMessage', text: 'No active objective in this conversation yet. Describe what you want to build to start one.' });
			return;
		}
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();
		if (!accessToken) return;

		this.post({ type: 'thinking', value: true, stage: 'Checking run status\u2026' });
		try {
			const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, task.activeCycleId);
			if (this.isCycleGone(statusRes.json as Record<string, unknown>, statusRes.status)) {
				await this.clearStaleActiveTask(
					`Cycle \`${task.activeCycleId}\` no longer exists (AIX service likely restarted).\n\n` +
					`Active run cleared. Describe what you want in **Agent** mode to start again.`,
				);
				return;
			}
			const item = statusRes.json.item || (statusRes.json as unknown as CycleItem);
			const phase = String(item?.phase || item?.status?.phase || task.currentState || 'UNKNOWN');
			task.currentState = phase;
			const files = (item?.proposed_files || []).map(f => f.name).filter(Boolean) as string[];
			if (files.length) task.changedFiles = [...new Set([...task.changedFiles, ...files])];

			let planLines = '';
			if (item?.execution_stages?.length) {
				task.plan = planStepsFromExecutionStages(item.execution_stages);
			} else {
				try {
					const planRes = await getCyclePlan(orchestratorUrl, accessToken, tenantId, task.activeCycleId);
					const steps = extractPlanSteps(planRes.json, phase);
					if (steps.length) task.plan = steps;
				} catch { /* optional */ }
			}
			const outcome = objectiveOutcome(item);
			task.plan = settlePlanForOutcome(task.plan || [], outcome);
			if (task.plan.length) planLines = '\n\n' + formatPlanMarkdown(task.plan);

			const done = task.plan.filter(s => s.status === 'done').length;
			const total = task.plan.length || 0;
			const running = task.plan.find(s => s.status === 'running');
			// AIX-REAL-EXECUTION-001/005: answer from `objective_status`, never from
			// `phase` alone — a `COMPLETED_FOR_REVIEW` orchestration phase with
			// `objective_status=COMPLETED` is a real "yes"; a `BLOCKED`/`FAILED`
			// objective_status is a real "no", not a bare "not yet" either.
			const head =
				outcome === 'completed' ? 'Yes — the objective is **COMPLETED**.'
				: outcome === 'completed_with_warnings' ? 'Yes, with warnings — the objective is **COMPLETED_WITH_WARNINGS**.'
				: outcome === 'blocked' ? `No — the objective is **BLOCKED**${item?.real_execute?.reason_code ? ` (\`${item.real_execute.reason_code}\`)` : ''}${item?.real_execute?.strategy_id ? ` · strategy \`${item.real_execute.strategy_id}\`` : ''}${item?.real_execute?.remaining_asked_work ? ' · remaining asked work' : ''}.`
				: outcome === 'failed' ? `No — the objective **FAILED**${item?.real_execute?.reason_code ? ` (${item.real_execute.reason_code})` : ''}.`
				: outcome === 'awaiting_review'
					? (phase.toUpperCase() === 'COMPLETED_FOR_REVIEW'
						? 'Run finished — phase is **COMPLETED_FOR_REVIEW** (review the result; Propose files stays empty for read-only work).'
						: 'Not yet — a proposal is **awaiting review**, no target operation has completed.')
				: 'Not yet.';

			const body = [
				head,
				'',
				total ? `${done} of ${total} steps are complete.` : `Current phase: **${phase}**.`,
				running ? `AIX is currently **${running.label}**.` : (task.currentStep ? `Current step: ${task.currentStep}` : ''),
				`${task.changedFiles.length} file${task.changedFiles.length === 1 ? '' : 's'} have changed.`,
				planLines,
				'',
				'<details><summary>Technical Details</summary>',
				'',
				`- Objective: \`${task.activeObjectiveId || '—'}\``,
				`- Cycle: \`${task.activeCycleId}\``,
				`- WorkHub: \`${task.workhubTaskId || '—'}\``,
				'',
				'</details>',
			].filter(Boolean).join('\n');

			await this.persistActiveTask(task);
			this.post({ type: 'assistantMessage', text: body });
		} finally {
			this.post({ type: 'thinking', value: false });
		}
	}

	private async replyWithDiffStatus(): Promise<void> {
		const changes = this.edits.summaries().filter(c => c.status === 'pending' || c.status === 'kept' || c.status === 'editing');
		const task = this.resolveActiveTask();
		const files = changes.length ? changes.map(c => c.relativePath) : (task?.changedFiles || []);
		if (!files.length) {
			this.post({ type: 'assistantMessage', text: 'No changed files to show yet for the active run. Approve the cycle (Resume) so AIX can produce files, then ask again.' });
			return;
		}
		const lines = ['### Changed files', '', ...files.map(f => `- \`${f}\``), '', 'Open the changes bar or run **AIX: Show Pending Changes** to review diffs.'];
		this.post({ type: 'assistantMessage', text: lines.join('\n') });
		if (changes[0]) await this.edits.openDiff(changes[0].filePath);
	}

	private async replyWithTestStatus(): Promise<void> {
		const task = this.resolveActiveTask();
		if (!task?.activeCycleId) {
			this.post({ type: 'assistantMessage', text: 'No active run — nothing to verify yet. Start an Agent objective first, then Approve/Resume if it is waiting for approval.' });
			return;
		}
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();
		if (!accessToken) return;
		const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, task.activeCycleId);
		const item = statusRes.json.item || (statusRes.json as unknown as CycleItem);
		const pos = Array.isArray(item?.tests?.positive) ? item!.tests!.positive!.length : 0;
		const neg = Array.isArray(item?.tests?.negative) ? item!.tests!.negative!.length : 0;
		task.testResults = { passed: pos, failed: 0, summary: `${pos} positive / ${neg} negative cases on cycle` };
		await this.persistActiveTask(task);
		this.post({
			type: 'assistantMessage',
			text: [
				'### Verification',
				'',
				task.testResults.summary,
				'',
				'_Completion still requires build, typecheck, unit/integration/tenant-isolation/migration tests, and secret checks — generated cases alone are not enough._',
			].join('\n'),
		});
	}

	/**
	 * Which `repository_id` an admitted objective targets. `aix.repositoryId` is an
	 * explicit override (matches an `OBJECTIVE_CYCLE_REPOSITORY_ROOTS` key exactly);
	 * otherwise falls back to the open workspace folder's name — the natural "edit
	 * the repo I currently have open" default. An id with no matching registry entry
	 * is not an error: the backend falls back to its existing metadata-only demo path
	 * for it rather than failing, so this never blocks admitting an objective.
	 */
	private resolveRepositoryId(): string {
		const config = vscode.workspace.getConfiguration('aix');
		const override = config.get<string>('repositoryId', '').trim();
		if (override) return override;
		return vscode.workspace.workspaceFolders?.[0]?.name || 'workspace';
	}

	/**
	 * AIX-REAL-EXECUTION-002: pick latest non-greeting user message as objective source.
	 * Never returns a pure greeting like "Hey".
	 */
	private resolveObjectiveBinding(objectiveText?: string): {
		objectiveText: string;
		selectedMessageId: string;
		messages: { message_id: string; role: 'user' | 'assistant'; content: string; parent_message_id?: string | null }[];
	} | null {
		const conv = this.currentConv;
		const msgs = conv?.messages || [];
		const payload = msgs.map((m, i) => ({
			message_id: m.message_id || `local-${conv?.id || 'c'}-${i}`,
			role: m.role,
			content: m.content,
			parent_message_id: m.parent_message_id ?? (i > 0 ? (msgs[i - 1].message_id || `local-${conv?.id || 'c'}-${i - 1}`) : null),
		}));

		const explicit = (objectiveText || '').trim();
		if (explicit && !isPureGreeting(explicit)) {
			const matching = [...payload].reverse().find(m => m.role === 'user' && m.content.trim() === explicit);
			const selectedMessageId = matching?.message_id || `local-selected-${Date.now()}`;
			const withExplicit = matching
				? payload
				: [
					...payload,
					{
						message_id: selectedMessageId,
						role: 'user' as const,
						content: explicit,
						parent_message_id: payload.length ? payload[payload.length - 1].message_id : null,
					},
				];
			return { objectiveText: explicit, selectedMessageId, messages: withExplicit };
		}

		const latestObjective = [...payload].reverse().find(m => m.role === 'user' && !isPureGreeting(m.content));
		if (!latestObjective) return null;
		return {
			objectiveText: latestObjective.content.trim(),
			selectedMessageId: latestObjective.message_id,
			messages: payload,
		};
	}

	private async handleAdmitAndRun(objectiveText?: string, taskClass?: string, proposedScope?: unknown): Promise<void> {
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();

		if (!accessToken) {
			this.post({ type: 'thinking', value: false });
			await this.auth.signIn();
			await this.postAuthState();
			return;
		}

		// One active objective per conversation — do not spawn duplicates
		const existing = this.resolveActiveTask();
		const existingPhase = (existing?.currentState || '').toUpperCase();
		if (existing?.activeCycleId && !isStoredActiveRunFinished(existingPhase)) {
			// Probe the service: after a restart the cycle is gone but local state still blocks.
			try {
				const probe = await getCycle(orchestratorUrl, accessToken, tenantId, existing.activeCycleId);
				if (this.isCycleGone(probe.json as Record<string, unknown>, probe.status)) {
					await this.clearStaleActiveTask(
						`Previous cycle \`${existing.activeCycleId}\` is gone (service restart). Starting a fresh run\u2026`,
					);
				} else if (AWAITING_USER_PHASES.has(existingPhase)) {
					this.post({
						type: 'assistantMessage',
						text: existingPhase === 'BLOCKED'
							? `This run is **BLOCKED**${existing.reasonCode ? ` (\`${existing.reasonCode}\`)` : ''}. Click **Resume** in the active-run panel to **retry** the same cycle — I will not start a second objective.`
							: `This run is **${existingPhase}**. Click **Resume** in the active-run panel to approve and continue — I will not start a second objective.`,
					});
					this.postActiveRun(existing);
					return;
				} else {
					this.post({
						type: 'assistantMessage',
						text: 'This conversation already has an active run. Ask **done?**, **show changes**, **pause**, or **cancel** — or start a **new** chat for a new objective.',
					});
					this.postActiveRun(existing);
					return;
				}
			} catch {
				// Network blip: keep the safety gate rather than spawning a duplicate.
				this.post({
					type: 'assistantMessage',
					text: 'This conversation already has an active run. Ask **done?**, **show changes**, **pause**, or **cancel** — or start a **new** chat for a new objective.',
				});
				this.postActiveRun(existing);
				return;
			}
		}

		this.activeAbortController = new AbortController();
		const signal = this.activeAbortController.signal;

		// AIX-REAL-EXECUTION-002: bind latest intended user message — never earlier "Hey".
		const bound = this.resolveObjectiveBinding(objectiveText);
		if (!bound) {
			this.post({
				type: 'assistantMessage',
				text: 'I need a concrete task (file path, edit, review, etc.) before I can start a run. A greeting alone is not an objective.',
			});
			return;
		}
		const redacted = redactSecrets(bound.objectiveText);
		const objective = redacted.text;
		const task_class = taskClass || 'bounded_repair';
		const repository_id = this.resolveRepositoryId();
		const clientMessageId = bound.selectedMessageId || `msg-${this.currentConv?.id || 'x'}-${Date.now()}`;

		this.post({ type: 'toolCall', id: 'admit', name: 'Starting run', status: 'running' });
		this.post({ type: 'thinking', value: true, stage: 'Planning & admitting objective\u2026' });

		try {
			const admitKey = `admit:${tenantId || 't'}:${this.currentConv?.id || 'c'}:${clientMessageId}`;
			const admitRes = await postAdmit(orchestratorUrl, accessToken, tenantId, {
				objective,
				task_class,
				repository_id,
				allowed_files: proposedScope,
				conversation_id: this.currentConv?.id,
				messages: bound.messages,
				selected_message_id: bound.selectedMessageId,
				objective_source_message_id: bound.selectedMessageId,
			}, signal, admitKey);

			if (signal.aborted) return;

			const errCode = admitErrorCode(admitRes.json);
			if (admitRes.status >= 400 || !admitRes.json.objective_id) {
				this.post({ type: 'toolCall', id: 'admit', name: 'Starting run', status: 'error', detail: errCode || `HTTP ${admitRes.status}` });
				const errDetail = String(
					(admitRes.json.error && typeof admitRes.json.error === 'object' && (admitRes.json.error as { detail?: unknown }).detail)
					|| admitRes.json.detail
					|| admitRes.json.message
					|| `HTTP ${admitRes.status}`,
				);
				if (errCode === 'INTENT_UNCLEAR' || errCode === 'WRONG_SOURCE_MESSAGE') {
					this.post({
						type: 'assistantMessage',
						text: [
							`**Clarification needed** (\`${errCode}\`)`,
							'',
							errDetail,
							'',
							'Reply with the concrete target (path, symbol, or what to change). I will not invent a plan or mark this complete.',
						].join('\n'),
					});
					return;
				}
				this.post({
					type: 'assistantMessage',
					text: '',
					error: {
						title: 'Could not start run',
						status: admitRes.status,
						causes: [errDetail, 'Ensure AIX-Frontend BFF is running at ' + orchestratorUrl],
						actions: ['Sign In to AIX', 'Open Settings'],
						raw: JSON.stringify(admitRes.json, null, 2),
					},
				});
				return;
			}

			const objectiveId = String(admitRes.json.objective_id);
			const taskFamily = admitRes.json.task_family ? String(admitRes.json.task_family) : undefined;
			const sourceMsgId = admitRes.json.objective_source_message_id
				? String(admitRes.json.objective_source_message_id)
				: bound.selectedMessageId;
			this.post({ type: 'toolCall', id: 'admit', name: 'Objective admitted', status: 'done', detail: taskFamily });
			this.post({ type: 'toolCall', id: 'cycle', name: 'Starting execution', status: 'running' });

			const cycleKey = `cycle:${tenantId || 't'}:${this.currentConv?.id || 'c'}:${objectiveId}`;
			const cycleRes = await postStartCycle(orchestratorUrl, accessToken, tenantId, objectiveId, signal, cycleKey);

			if (signal.aborted) return;

			if (cycleRes.status >= 400 || !cycleRes.json.cycle_id) {
				this.post({ type: 'toolCall', id: 'cycle', name: 'Starting execution', status: 'error', detail: `HTTP ${cycleRes.status}` });
				this.post({ type: 'assistantMessage', text: `Admitted, but the cycle could not start (HTTP ${cycleRes.status}).` });
				return;
			}

			const cycleId = String(cycleRes.json.cycle_id);
			this.post({ type: 'toolCall', id: 'cycle', name: 'Execution started', status: 'done' });

			const task: ActiveTaskState = {
				conversationId: this.currentConv?.id || uid(),
				activeObjectiveId: objectiveId,
				activeCycleId: cycleId,
				workhubTaskId: admitRes.json.workhub_task_id ? String(admitRes.json.workhub_task_id) : undefined,
				currentState: 'RUNNING',
				currentStep: 'Starting pipeline',
				// Placeholder only until getCycle returns backend execution_stages (Sprint 005).
				plan: [
					{ id: 'pending', label: 'Waiting for backend stages\u2026', status: 'running' },
				],
				changedFiles: [],
				lastClientMessageId: clientMessageId,
				objectiveSummary: trunc(objective, 120),
				taskFamily,
				objectiveSourceMessageId: sourceMsgId,
			};
			await this.persistActiveTask(task);

			this.post({
				type: 'assistantMessage',
				text: [
					`Started working on: **${trunc(objective, 100)}**`,
					'',
					formatPlanMarkdown(task.plan),
					'',
					'Ask **done?** anytime for progress. Use the active-run panel to Pause / Cancel / review diffs.',
					'',
					'<details><summary>Technical Details</summary>',
					'',
					`- Objective: \`${objectiveId}\``,
					`- Cycle: \`${cycleId}\``,
					`- WorkHub: \`${task.workhubTaskId || '—'}\``,
					taskFamily ? `- Task family: \`${taskFamily}\`` : '',
					sourceMsgId ? `- Source message: \`${sourceMsgId}\`` : '',
					`- Repository: \`${repository_id}\` _(must match a key registered in the backend's OBJECTIVE_CYCLE_REPOSITORY_ROOTS for real execution — otherwise AIX blocks or proposes only)_`,
					redacted.redacted ? `- Secrets redacted before submit: ${redacted.kinds.join(', ')}` : '',
					'',
					'</details>',
				].filter(Boolean).join('\n'),
			});

			await this.watchCycle(orchestratorUrl, accessToken, tenantId, cycleId, signal);
		} catch (err) {
			if (signal.aborted) return;
			this.post({ type: 'toolCall', id: 'admit', name: 'Starting run', status: 'error', detail: 'Failed' });
			this.post({
				type: 'assistantMessage',
				text: '',
				error: {
					title: 'Execution Service Unavailable',
					causes: [
						'Could not connect to AIX execution service at ' + orchestratorUrl,
						err instanceof Error ? err.message : String(err),
					],
					actions: ['Open Settings', 'Sign In to AIX'],
				},
			});
		} finally {
			this.post({ type: 'thinking', value: false });
			this.activeAbortController = undefined;
		}
	}

	/**
	 * Poll cycle status/events and surface proposed file contents in the chat + editor.
	 */
	private async watchCycle(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		cycleId: string,
		signal: AbortSignal,
	): Promise<void> {
		if (this.watchingCycles.has(cycleId)) {
			// Already being watched by another in-flight call (e.g. the loop started at
			// admission is still running when a Resume/Approve click starts a second
			// one) — do not start a duplicate loop with its own independent dedup state.
			return;
		}
		this.watchingCycles.add(cycleId);
		try {
			await this.runWatchCycleLoop(orchestratorUrl, accessToken, tenantId, cycleId, signal);
		} finally {
			this.watchingCycles.delete(cycleId);
		}
	}

	private async runWatchCycleLoop(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		cycleId: string,
		signal: AbortSignal,
	): Promise<void> {
		const POLL_MS = 3000;
		const MAX_POLLS = 80; // ~4 minutes
		const seenEvents = new Set<string>();
		const shownFiles = new Set<string>();
		// Fires the auto-approve call (and its chat message) at most once per
		// cycle+phase — without this, every poll tick while still WAITING_APPROVAL
		// re-posts "Auto-approving..." and re-calls approveAndContinue, which is the
		// literal "repeated approval messages" bug this fixes on the client side
		// (the server-side `approval_request_id` idempotency guard is the other half).
		const approvalAttempted = new Set<string>();
		let lastPhase = '';

		this.post({ type: 'toolCall', id: 'watch', name: 'Watching cycle execution', status: 'running', detail: cycleId });
		this.post({ type: 'thinking', value: true, stage: 'Watching AIX cycle\u2026' });

		for (let i = 0; i < MAX_POLLS; i++) {
			if (signal.aborted) {
				this.post({ type: 'toolCall', id: 'watch', name: 'Watching cycle execution', status: 'error', detail: 'Cancelled' });
				return;
			}

			let item: CycleItem | undefined;
			try {
				const statusRes = await getCycle(orchestratorUrl, accessToken, tenantId, cycleId, signal);
				if (this.isCycleGone(statusRes.json as Record<string, unknown>, statusRes.status)) {
					this.post({ type: 'toolCall', id: 'watch', name: 'Watching cycle execution', status: 'error', detail: 'CYCLE_NOT_FOUND' });
					await this.clearStaleActiveTask(
						`Cycle \`${cycleId}\` disappeared while watching (AIX service restart).\n\n` +
						`Active run cleared. Start again in **Agent** mode.`,
					);
					return;
				}
				if (statusRes.status < 400) {
					item = statusRes.json.item || (statusRes.json as unknown as CycleItem);
				}
			} catch {
				// keep polling through transient errors
			}

			const phase = String(item?.phase || item?.status?.phase || '').toUpperCase() || 'UNKNOWN';
			if (this.currentConv?.activeTask) {
				let planDirty = false;
				if (item?.execution_stages?.length) {
					const stagePlan = planStepsFromExecutionStages(item.execution_stages);
					if (stagePlan.length) {
						this.currentConv.activeTask.plan = stagePlan;
						const running = stagePlan.find(s => s.status === 'running') || stagePlan.find(s => s.status === 'error');
						if (running) this.currentConv.activeTask.currentStep = running.label;
						const blocked = item.execution_stages.find(s => s.status === 'blocked' || s.status === 'failed');
						if (blocked?.reason_code) this.currentConv.activeTask.reasonCode = String(blocked.reason_code);
						planDirty = true;
					}
				}
				// Even without stages, clear stuck ● chips once the cycle is done for review.
				if (isObjectiveTerminal(item) && this.currentConv.activeTask.plan?.length) {
					const settled = settlePlanForOutcome(this.currentConv.activeTask.plan, objectiveOutcome(item));
					this.currentConv.activeTask.plan = settled;
					planDirty = true;
				}
				if (planDirty) {
					void this.persistActiveTask(this.currentConv.activeTask);
				}
			}
			if (phase && phase !== lastPhase) {
				lastPhase = phase;
				const outcome = objectiveOutcome(item);
				const phaseStatus =
					outcome === 'completed' || outcome === 'completed_with_warnings' || outcome === 'awaiting_review' ? 'done'
						: outcome === 'blocked' || outcome === 'failed' || TERMINAL_CYCLE_PHASES.has(phase) && phase !== 'COMPLETED' ? 'error'
							: 'running';
				this.post({
					type: 'toolCall',
					id: `phase-${phase}`,
					name: activityLabelForPhase(phase),
					status: phaseStatus,
					detail: item?.real_execute?.reason_code || item?.terminal_reason ? String(item?.real_execute?.reason_code || item?.terminal_reason) : undefined,
				});
				this.post({ type: 'thinking', value: true, stage: activityLabelForPhase(phase) });
				if (this.currentConv?.activeTask) {
					this.currentConv.activeTask.currentState = phase;
					this.currentConv.activeTask.currentStep = activityLabelForPhase(phase);
					if (item?.real_execute?.reason_code) {
						this.currentConv.activeTask.reasonCode = String(item.real_execute.reason_code);
					}
					const files = (item?.proposed_files || []).map(f => f.name).filter(Boolean) as string[];
					if (files.length) {
						this.currentConv.activeTask.changedFiles = [...new Set([...this.currentConv.activeTask.changedFiles, ...files])];
					}
					if (this.currentConv.activeTask.plan?.length) {
						this.currentConv.activeTask.plan = settlePlanForOutcome(
							this.currentConv.activeTask.plan,
							outcome,
						);
					}
					void this.persistActiveTask(this.currentConv.activeTask);
				}
			}

			// Events timeline — real discovery/tool events (event_type prefixed
			// "discovery."/"tool.") drive one live, in-place-updating run panel
			// (Inspecting repository / Reading .../ Searching .../ Editing real
			// application files / Running validation / Preparing diff) instead of a
			// new chip per event; everything else keeps its own per-event chip.
			try {
				const eventsRes = await getCycleEvents(orchestratorUrl, accessToken, tenantId, cycleId, signal);
				const events = Array.isArray(eventsRes.json.events) ? eventsRes.json.events : [];
				const fresh = events.filter(ev => {
					const key = eventKey(ev);
					if (seenEvents.has(key)) return false;
					seenEvents.add(key);
					return true;
				});
				for (const ev of fresh) {
					const evType = String(ev.event_type || ev.event || '');
					if (evType.startsWith('discovery.') || evType.startsWith('tool.')) {
						this.post({
							type: 'toolCall',
							id: 'run-panel',
							name: runPanelLabel(ev),
							status: 'running',
						});
						continue;
					}
					const label = String(ev.event || ev.event_type || 'event');
					const adminNoise = /^(cycle\.|request\.|plan\.generated|objective\.created)/i.test(label)
						|| /bounded_propose|workhub_load|administrative/i.test(label);
					const failed = label.toLowerCase().includes('fail') || label.toLowerCase().includes('error') || label.toLowerCase().includes('blocked');
					this.post({
						type: 'toolCall',
						id: `ev-${eventKey(ev)}`,
						name: label,
						// Never paint admin lifecycle events green as if work completed.
						status: failed ? 'error' : adminNoise ? 'running' : 'done',
						detail: String(ev.at || ev.occurred_at || ''),
					});
				}
			} catch {
				// optional panel
			}

			await this.surfaceProposedFiles(
				orchestratorUrl, accessToken, tenantId, cycleId, item, shownFiles, signal,
			);

			// Auto-approve WAITING_APPROVAL so Agent mode can produce files \u2014 at most
			// once per cycle+phase; a phase that stays WAITING_APPROVAL across polls
			// (e.g. a transient error) must not re-fire the approve call or re-post
			// the chat message on every tick.
			if (AWAITING_USER_PHASES.has(phase) && (phase === 'WAITING_APPROVAL' || phase === 'AWAITING_APPROVAL')) {
				const approvalKey = `${cycleId}:${phase}`;
				if (approvalAttempted.has(approvalKey)) {
					await delay(POLL_MS);
					continue;
				}
				approvalAttempted.add(approvalKey);

				const task = this.resolveActiveTask() || {
					conversationId: this.currentConv?.id || uid(),
					activeCycleId: cycleId,
					activeObjectiveId: item?.objective_id ? String(item.objective_id) : undefined,
					currentState: phase,
					plan: [],
					changedFiles: [],
					pendingApproval: true,
				};
				task.activeCycleId = cycleId;
				task.currentState = phase;
				task.pendingApproval = true;
				await this.persistActiveTask(task);

				this.post({
					type: 'toolCall',
					id: 'watch',
					name: 'Waiting for approval',
					status: 'running',
					detail: 'Auto-approving for Studio Agent',
				});
				this.post({
					type: 'assistantMessage',
					text: 'Cycle is **WAITING_APPROVAL**. Auto-approving with a local Studio reference so execution can continue and files can land in your workspace\u2026',
				});

				const ok = await this.approveAndContinue(orchestratorUrl, accessToken, tenantId, task);
				if (!ok) {
					this.post({
						type: 'toolCall',
						id: 'watch',
						name: 'Watching cycle execution',
						status: 'error',
						detail: phase,
					});
					return;
				}
				// Keep polling after approval
				await delay(800);
				continue;
			}

			if (isObjectiveTerminal(item)) {
				// AIX-REAL-EXECUTION-001/005: `objective_status` (when present) is the
				// truthful signal, not the orchestration `phase` — the real terminal
				// phase on a successful run is `COMPLETED_FOR_REVIEW`, which is not in
				// `TERMINAL_CYCLE_PHASES`, so relying on phase alone here previously
				// meant a genuinely completed objective could poll until timeout.
				const outcome = objectiveOutcome(item);
				const succeeded =
					outcome === 'completed'
					|| outcome === 'completed_with_warnings'
					|| outcome === 'awaiting_review';
				this.post({
					type: 'toolCall',
					id: 'watch',
					name: 'Watching cycle execution',
					status: succeeded ? 'done' : 'error',
					detail: outcome === 'unknown' ? phase : objectiveOutcomeLabel(outcome),
				});
				// Close the live run-panel chip so it does not stay ● forever.
				this.post({
					type: 'toolCall',
					id: 'run-panel',
					name: activityLabelForPhase(phase),
					status: succeeded ? 'done' : 'error',
				});
				this.post({ type: 'thinking', value: false });

				if (this.currentConv?.activeTask) {
					this.currentConv.activeTask.currentState = phase;
					this.currentConv.activeTask.currentStep = activityLabelForPhase(phase);
					if (item?.execution_stages?.length) {
						this.currentConv.activeTask.plan = planStepsFromExecutionStages(item.execution_stages);
					}
					this.currentConv.activeTask.plan = settlePlanForOutcome(
						this.currentConv.activeTask.plan || [],
						outcome,
					);
					// Persist briefly so Status/Diff still work, then drop the live bar
					// so the next panel open does not resurrect a finished zombie run.
					await this.persistActiveTask(this.currentConv.activeTask);
				}

				const summary = await this.buildCycleSummary(orchestratorUrl, accessToken, tenantId, cycleId, item, shownFiles, signal);
				this.post({ type: 'assistantMessage', text: summary });
				const finished = this.resolveActiveTask();
				if (finished?.activeCycleId === cycleId) {
					await this.dismissFinishedActiveRun(finished);
				}
				return;
			}

			await delay(POLL_MS);
		}

		this.post({ type: 'toolCall', id: 'watch', name: 'Watching cycle execution', status: 'error', detail: 'Timed out' });
		this.post({
			type: 'assistantMessage',
			text: `Timed out waiting for cycle \`${cycleId}\` to finish (last phase: **${lastPhase || 'unknown'}**).\n\nOpen the AIX Execution page for full detail, or re-run Agent mode.`,
		});
	}

	private async surfaceProposedFiles(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		cycleId: string,
		item: CycleItem | undefined,
		shownFiles: Set<string>,
		signal: AbortSignal,
	): Promise<number> {
		let newlyShown = 0;

		// Real read-file branch: nothing changed on disk (there is nothing to open/
		// diff), so this must be reported explicitly — otherwise it silently falls
		// through to the demo-file guessing below and the user sees nothing at all.
		const readFile = item?.real_execute?.read_file;
		if (readFile) {
			const shownKey = `real-read:${cycleId}`;
			if (!shownFiles.has(shownKey)) {
				shownFiles.add(shownKey);
				newlyShown += this.presentRealFileRead(item!, readFile);
			}
			return newlyShown;
		}

		// Real source-edit branch, propose stage: a real, computed change against real
		// discovered content, not yet applied. Write it into the actual repository file
		// via the same propose/Keep/Undo flow as any other edit — this is the review
		// gate: the user sees it land in the editor with inline highlights and decides
		// whether to Keep or Undo, exactly like the demo path already works, just
		// pointed at the real file instead of an `aix-generated` side copy.
		const proposedEdits = item?.real_execute?.proposed_edits || [];
		if (proposedEdits.length) {
			const shownKey = `real-propose:${cycleId}`;
			if (!shownFiles.has(shownKey)) {
				shownFiles.add(shownKey);
				newlyShown += await this.presentProposedSourceEdits(item!, proposedEdits);
			}
			return newlyShown;
		}

		// Real source-edit branch, legacy applied-already shape: `changed_source_files`
		// are genuine edits already applied to an existing file in the approved target
		// repository — show the real file and a real diff, never the `aix-generated`
		// side-folder flow used by the legacy metadata/demo path below. Kept for any
		// evidence produced by an older backend version; the current backend always
		// takes the propose branch above instead.
		const changedSourceFiles = item?.real_execute?.changed_source_files || [];
		if (changedSourceFiles.length) {
			const shownKey = `real-source:${cycleId}`;
			if (!shownFiles.has(shownKey)) {
				shownFiles.add(shownKey);
				newlyShown += await this.presentRealSourceEdits(
					orchestratorUrl, accessToken, tenantId, cycleId, item!, signal,
				);
			}
			return newlyShown;
		}

		const proposed = Array.isArray(item?.proposed_files) ? item!.proposed_files! : [];
		const localRoot = item?.real_execute?.workspace;
		const artifactPaths = item?.real_execute?.artifacts || {};

		const localCandidates: { name: string; filePath: string }[] = [];
		if (localRoot) {
			for (const name of ['index.html', 'styles.css', 'plan.json', 'execution_report.json']) {
				const filePath = name.endsWith('.json')
					? path.join(localRoot, 'evidence', name === 'plan.json' ? 'plan.json' : 'execution_report.json')
					: path.join(localRoot, 'proposed', name);
				localCandidates.push({ name, filePath });
			}
			for (const [key, p] of Object.entries(artifactPaths)) {
				if (typeof p === 'string' && p.length) {
					localCandidates.push({ name: path.basename(p) || key, filePath: p });
				}
			}
		}

		for (const cand of localCandidates) {
			if (shownFiles.has(cand.filePath) || shownFiles.has(cand.name)) continue;
			try {
				const content = await fs.readFile(cand.filePath, 'utf8');
				shownFiles.add(cand.filePath);
				shownFiles.add(cand.name);
				newlyShown++;
				await this.presentFileEdit(cycleId, cand.name, content);
			} catch {
				// file not ready yet
			}
		}

		const names = proposed.length
			? proposed.map(f => f.name).filter(Boolean)
			: ['index.html', 'styles.css'];

		for (const name of names) {
			if (shownFiles.has(name)) continue;
			if (name !== 'index.html' && name !== 'styles.css') continue;
			try {
				const preview = await getCyclePreview(orchestratorUrl, accessToken, tenantId, cycleId, name, signal);
				if (preview.status >= 400 || !preview.text.trim()) continue;
				if (preview.text.trim().startsWith('{') && preview.text.includes('"ok": false')) continue;
				shownFiles.add(name);
				newlyShown++;
				await this.presentFileEdit(cycleId, name, preview.text);
			} catch {
				// preview not ready
			}
		}

		return newlyShown;
	}

	/**
	 * Write each proposed real-repository edit into the actual file via the same
	 * propose/Keep/Undo session the demo path already uses — `editSession.beginEdit`
	 * reads the file's true current content as `originalContent` (the backend never
	 * touched it), then `applyProposed` writes `new_content`, decorates it, and opens
	 * the diff. Keep finalizes it; Undo restores the real file to what it actually was
	 * — this is the human review gate, not the backend's `WAITING_APPROVAL` auto-resume
	 * (that gate is about permission to inspect/propose at all, not about accepting
	 * this specific change).
	 */
	private async presentProposedSourceEdits(
		item: CycleItem,
		proposedEdits: NonNullable<NonNullable<CycleItem['real_execute']>['proposed_edits']>,
	): Promise<number> {
		const repositoryRoot = item.real_execute?.repository_root;
		if (!repositoryRoot) {
			this.post({
				type: 'assistantMessage',
				text: `### ${proposedEdits.length} file edit${proposedEdits.length === 1 ? '' : 's'} proposed\n\n_Repository root missing from the response — cannot locate the real file(s) to review._`,
			});
			return 0;
		}

		this.post({
			type: 'toolCall',
			id: 'run-panel',
			name: `Proposing ${proposedEdits.length} real application file edit${proposedEdits.length === 1 ? '' : 's'}`,
			status: 'done',
		});

		const task = this.resolveActiveTask();
		let applied = 0;
		for (const edit of proposedEdits) {
			const absolutePath = path.join(repositoryRoot, edit.path);
			try {
				await this.edits.beginEdit(absolutePath, edit.path);
				const summary = await this.edits.applyProposed(absolutePath, edit.new_content, { openDiff: true, reveal: true });
				applied++;
				this.post({
					type: 'toolCall',
					id: `edit-${edit.path}`,
					name: `Proposed ${summary.relativePath}`,
					status: 'done',
					detail: `+${summary.addedLines}/−${summary.removedLines}`,
				});
				if (task) task.changedFiles = [...new Set([...task.changedFiles, edit.path])];
			} catch (err) {
				this.post({
					type: 'toolCall',
					id: `edit-${edit.path}`,
					name: `Could not open ${edit.path}`,
					status: 'error',
					detail: err instanceof Error ? err.message : String(err),
				});
			}
		}
		if (task) await this.persistActiveTask(task);

		const lines = [
			`### Proposed ${applied} real file edit${applied === 1 ? '' : 's'} in \`${path.basename(repositoryRoot)}\``,
			'',
			...proposedEdits.map(e => `- \`${e.path}\`${e.reason ? ` — ${e.reason}` : ''}`),
			'',
			'Opened in the editor with inline highlights — nothing is final yet. Review each file and use **Keep** to accept or **Undo** to discard (changes bar, or `AIX: Show Pending Changes`).',
		];
		this.post({ type: 'assistantMessage', text: lines.join('\n') });
		return applied;
	}

	/**
	 * Present real edits already applied by the backend to an existing file inside
	 * the approved target repository — opens the actual real file (not a copy under
	 * `aix-generated`) and shows the real git diff. `run_artifacts` (internal AIX
	 * bookkeeping) are shown separately, collapsed, never mixed into the diff. Legacy
	 * path, kept for evidence produced by an older backend version.
	 */
	private async presentRealSourceEdits(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		cycleId: string,
		item: CycleItem,
		signal: AbortSignal,
	): Promise<number> {
		const changedSourceFiles = item.real_execute?.changed_source_files || [];
		const repositoryRoot = item.real_execute?.repository_root;
		const runArtifacts = item.real_execute?.run_artifacts || [];

		this.post({
			type: 'toolCall',
			id: 'run-panel',
			name: `Editing ${changedSourceFiles.length} real application file${changedSourceFiles.length === 1 ? '' : 's'}`,
			status: 'done',
		});

		let diffText = item.real_execute?.diff || '';
		if (!diffText) {
			try {
				const diffRes = await getCycleDiff(orchestratorUrl, accessToken, tenantId, cycleId, signal);
				diffText = diffRes.json.diff || '';
			} catch {
				// fall back to whatever the status payload already had (possibly empty)
			}
		}

		const task = this.resolveActiveTask();
		for (const changed of changedSourceFiles) {
			if (!repositoryRoot || !changed.path) continue;
			const absolutePath = path.join(repositoryRoot, changed.path);
			try {
				const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(absolutePath));
				await vscode.window.showTextDocument(doc, { preview: false, preserveFocus: true });
			} catch {
				// file may not be reachable from this window (different host/workspace) — still report it
			}
			if (task) {
				task.changedFiles = [...new Set([...task.changedFiles, changed.path])];
			}
		}
		if (task) await this.persistActiveTask(task);

		const lines = [
			`### Editing ${changedSourceFiles.length} real file${changedSourceFiles.length === 1 ? '' : 's'} in \`${repositoryRoot ? path.basename(repositoryRoot) : 'repository'}\``,
			'',
			...changedSourceFiles.map(f => `- \`${f.path}\` \`+${f.additions ?? 0}\` / \`−${f.deletions ?? 0}\``),
			'',
		];
		if (diffText.trim()) {
			lines.push('```diff', trimContent(diffText, 4000), '```', '');
		}
		if (runArtifacts.length) {
			lines.push(
				'<details><summary>Run artifacts (internal — not source changes)</summary>',
				'',
				...runArtifacts.map(p => `- \`${p}\``),
				'',
				'</details>',
			);
		}
		this.post({ type: 'assistantMessage', text: lines.join('\n') });
		return changedSourceFiles.length;
	}

	/**
	 * Present a real read-file result. No file changed (nothing to open/diff), so this
	 * is a chat summary only — and, for `.env`-shaped files, key names/presence/
	 * sensitivity/type only. A sensitive key's value is never present anywhere in
	 * `readFile` to begin with (see `read_file_execute.py::summarize_env_content`), so
	 * there is nothing to accidentally print here even by mistake.
	 */
	private presentRealFileRead(item: CycleItem, readFile: NonNullable<CycleItem['real_execute']>['read_file']): number {
		if (!readFile) return 0;
		const verification = item.real_execute?.read_verification;
		this.post({
			type: 'toolCall',
			id: 'run-panel',
			name: `Read ${readFile.resource}`,
			status: verification && Object.values(verification).every(Boolean) ? 'done' : 'error',
		});

		const lines = [
			`### Read \`${readFile.resource}\``,
			'',
			`${readFile.bytes_read} bytes, ${readFile.line_count} line${readFile.line_count === 1 ? '' : 's'}.`,
			'',
		];
		const env = readFile.env_summary;
		if (env) {
			const sensitive = env.keys.filter(k => k.sensitive);
			const ordinary = env.keys.filter(k => !k.sensitive);
			lines.push(`**${env.keys.length} variable${env.keys.length === 1 ? '' : 's'} found**, ${sensitive.length} sensitive (value withheld):`, '');
			for (const k of sensitive) {
				lines.push(`- \`${k.name}\` — present: ${k.present ? 'yes' : 'no'} _(sensitive — value never sent to chat)_`);
			}
			for (const k of ordinary) {
				lines.push(`- \`${k.name}\` — present: ${k.present ? 'yes' : 'no'}${k.value_type ? `, type: ${k.value_type}` : ''}`);
			}
			if (env.duplicate_keys.length) {
				lines.push('', `Duplicate keys: ${env.duplicate_keys.map(k => `\`${k}\``).join(', ')}`);
			}
			if (env.malformed_lines.length) {
				lines.push('', `Malformed line(s): ${env.malformed_lines.join(', ')}`);
			}
		}
		this.post({ type: 'assistantMessage', text: lines.join('\n') });
		return 1;
	}

	/** Cursor-like: write into the real workspace, decorate, open diff. */
	private async presentFileEdit(cycleId: string, name: string, content: string): Promise<void> {
		const targets = await this.resolveWorkspaceEditPaths(cycleId, name);
		if (!targets.primary) {
			this.post({
				type: 'assistantMessage',
				text: `### Proposed: \`${name}\`\n\n\`\`\`${langFor(name)}\n${trimContent(content)}\n\`\`\`\n\n_Open a workspace folder so AIX can write files into the editor._`,
			});
			return;
		}

		this.post({ type: 'toolCall', id: `edit-${name}`, name: `Editing ${path.basename(targets.primary)}`, status: 'running' });
		this.post({ type: 'thinking', value: true, stage: `Editing ${targets.relative}\u2026` });
		await this.edits.beginEdit(targets.primary, name);

		const summary = await this.edits.applyProposed(targets.primary, content, { openDiff: true, reveal: true });
		if (targets.mirror && targets.mirror !== targets.primary) {
			try {
				await fs.mkdir(path.dirname(targets.mirror), { recursive: true });
				await fs.writeFile(targets.mirror, content, 'utf8');
			} catch { /* best-effort mirror */ }
		}

		const task = this.resolveActiveTask();
		if (task) {
			task.changedFiles = [...new Set([...task.changedFiles, summary.relativePath])];
			await this.persistActiveTask(task);
		}

		this.post({
			type: 'toolCall',
			id: `edit-${name}`,
			name: `Edited ${summary.relativePath}`,
			status: 'done',
			detail: `${summary.isNew ? 'new' : 'modified'}  +${summary.addedLines}/−${summary.removedLines}`,
		});
		this.post({
			type: 'assistantMessage',
			text: [
				`### Editing \`${summary.relativePath}\``,
				'',
				summary.isNew ? '_New file in your workspace_' : '_Modified file in your workspace_',
				`\`+${summary.addedLines}\` / \`−${summary.removedLines}\``,
				'',
				'Opened in the editor with inline highlights. Use **Keep** / **Undo** in the changes bar.',
			].join('\n'),
		});
	}

	/**
	 * Map AIX proposed artifacts into real workspace paths (Explorer-visible),
	 * not only under a hidden .aix cache.
	 */
	private async resolveWorkspaceEditPaths(
		cycleId: string,
		name: string,
	): Promise<{ primary?: string; mirror?: string; relative: string }> {
		const folder = vscode.workspace.workspaceFolders?.[0];
		if (!folder) return { relative: name };

		const safeName = name.replace(/[\\/]/g, path.sep).replace(/\.\./g, '');
		const slug = cycleId.replace(/^cyc_/, '').slice(0, 10);
		const generatedDir = path.join(folder.uri.fsPath, 'aix-generated', slug);
		const primary = path.join(generatedDir, safeName);
		const mirror = path.join(folder.uri.fsPath, '.aix', 'cycles', cycleId, 'proposed', path.basename(safeName));

		try {
			await fs.mkdir(path.dirname(primary), { recursive: true });
		} catch {
			return { relative: name };
		}

		return {
			primary,
			mirror,
			relative: vscode.workspace.asRelativePath(primary, false),
		};
	}

	private async buildCycleSummary(
		orchestratorUrl: string,
		accessToken: string,
		tenantId: string | undefined,
		cycleId: string,
		item: CycleItem | undefined,
		shownFiles: Set<string>,
		signal: AbortSignal,
	): Promise<string> {
		await this.surfaceProposedFiles(orchestratorUrl, accessToken, tenantId, cycleId, item, shownFiles, signal);

		const phase = String(item?.phase || item?.status?.phase || 'UNKNOWN');
		const outcome = objectiveOutcome(item);
		const re = item?.real_execute;
		const report = item?.final_report;
		const lines: string[] = [
			`## ${objectiveOutcomeLabel(outcome)}`,
			'',
			`- **Cycle ID:** \`${cycleId}\` _(orchestration phase: ${phase})_`,
		];
		if (item?.objective_id) lines.push(`- **Objective:** \`${item.objective_id}\``);
		if (item?.workhub_task_id) lines.push(`- **WorkHub Task:** \`${item.workhub_task_id}\``);
		if (re?.orchestration_status || re?.planning_status || re?.execution_status || re?.verification_status || re?.objective_status) {
			lines.push(
				`- **Statuses:** orch=\`${re?.orchestration_status || '—'}\` · plan=\`${re?.planning_status || '—'}\` · exec=\`${re?.execution_status || '—'}\` · verify=\`${re?.verification_status || '—'}\` · objective=\`${re?.objective_status || '—'}\``,
			);
		}
		// AIX-REAL-EXECUTION-001/005: real vs administrative always separate.
		const realOps = report?.real_operations;
		if (realOps || (re && (re.real_operations !== undefined || re.target_files_read !== undefined || re.target_files_modified !== undefined))) {
			lines.push(
				`- **Real operations:** ${realOps?.real_operations ?? re?.real_operations ?? 0} _(files read: ${realOps?.target_files_read ?? re?.target_files_read ?? 0}, files modified: ${realOps?.target_files_modified ?? re?.target_files_modified ?? 0}, commands: ${realOps?.commands_executed ?? 0}, tests: ${realOps?.tests_executed ?? 0})_`,
			);
		}
		if (typeof re?.administrative_artifacts_generated === 'number' || report?.administrative_artifacts?.length) {
			lines.push(
				`- **Administrative artifacts:** ${re?.administrative_artifacts_generated ?? report?.administrative_artifacts?.length ?? 0} _(plans/reports/evidence — not target work)_`,
			);
		}
		if (item?.execution_stages?.length) {
			lines.push('', '### Stages');
			for (const stage of item.execution_stages) {
				const mark = stage.status === 'completed' || stage.status === 'skipped' ? '✓'
					: stage.status === 'blocked' || stage.status === 'failed' ? '✗'
						: stage.status === 'running' ? '…' : '·';
				lines.push(`${mark} **${stage.stage}** — ${stage.status}${stage.reason_code ? ` (\`${stage.reason_code}\`)` : ''}`);
			}
		}
		const blockReason = report?.reason_code || re?.reason_code || item?.terminal_reason;
		if ((outcome === 'blocked' || outcome === 'failed') && blockReason) {
			lines.push(`- **Reason:** \`${blockReason}\`${report?.blocking_stage ? ` at **${report.blocking_stage}**` : ''}`);
		} else if (item?.terminal_reason) {
			lines.push(`- **Reason:** ${item.terminal_reason}`);
		}
		if (report?.recommended_next_action) {
			lines.push(`- **Next action:** ${report.recommended_next_action}`);
		}
		if (item?.real_execute?.workspace) lines.push(`- **Workspace:** \`${item.real_execute.workspace}\``);

		const checks = report?.verification_results;
		if (checks && checks.length) {
			lines.push('', '### Verification');
			for (const check of checks) {
				const mark = check.status === 'PASSED' ? '✓' : '✗';
				lines.push(`${mark} ${check.check_type}${check.failure_reason ? ` — ${check.failure_reason}` : ''}`);
			}
		}
		if (report?.administrative_artifacts?.length) {
			lines.push('', '### Administrative artifacts (not target evidence)');
			for (const a of report.administrative_artifacts.slice(0, 12)) {
				lines.push(`- \`${a}\``);
			}
		}

		const changes = this.edits.summaries().filter(c => c.status === 'pending' || c.status === 'kept');
		if (changes.length) {
			lines.push('', '### Code changes');
			for (const c of changes) {
				lines.push(`- \`${c.relativePath}\`  \`+${c.addedLines}\`/\`−${c.removedLines}\`${c.isNew ? ' _(new)_' : ''}`);
			}
			lines.push('', 'Pending edits are highlighted in the editor. Use the **changes bar** to Keep / Undo / open Diff.');
		} else {
			const shortNames = [...new Set([...shownFiles].map(f => path.basename(f)))];
			if (shortNames.length) {
				lines.push('', '### Files produced', ...shortNames.map(n => `- \`${n}\``));
			} else {
				lines.push('', '_No proposed file contents were available yet._');
			}
		}

		if (item?.tests) {
			const pos = Array.isArray(item.tests.positive) ? item.tests.positive.length : 0;
			const neg = Array.isArray(item.tests.negative) ? item.tests.negative.length : 0;
			if (pos || neg) lines.push('', `Tests: ${pos} positive / ${neg} negative cases generated.`);
		}

		return lines.join('\n');
	}

	/* ── Send ─────────────────────────────────────────────────────────────────── */

	private async handleSend(text: string, mode: string): Promise<void> {
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();

		if (!accessToken) {
			this.post({ type: 'thinking', value: false });
			this.post({
				type: 'assistantMessage',
				text: '',
				error: {
					title: 'Not Signed In',
					causes: ['You must sign in to AIX before starting a chat or executing actions'],
					actions: ['Sign In to AIX'],
				},
			});
			return;
		}

		const scrubbed = redactSecrets(text);
		const safeText = scrubbed.text;
		if (scrubbed.redacted) {
			this.post({ type: 'toolCall', id: 'redact', name: 'Redacted secrets before send', status: 'done', detail: scrubbed.kinds.join(', ') });
		}

		if (!this.currentConv) {
			this.currentConv = {
				id: uid(), title: trunc(safeText.replace(/^\/\w+\s*/, ''), 55),
				preview: trunc(safeText, 80), timestamp: Date.now(), messages: [], mode,
			};
		}
		this.currentConv.messages.push({
			role: 'user',
			content: safeText,
			message_id: `msg-${this.currentConv.id}-${Date.now()}`,
			parent_message_id: this.currentConv.messages.length
				? (this.currentConv.messages[this.currentConv.messages.length - 1].message_id || null)
				: null,
		});
		this.currentConv.mode = mode;

		const active = this.currentConv.activeTask;
		const hasActive = Boolean(active?.activeObjectiveId || active?.activeCycleId);
		const intent = classifyAgentIntent(safeText, {
			hasActiveObjective: Boolean(active?.activeObjectiveId),
			hasActiveCycle: Boolean(active?.activeCycleId) && !isStoredActiveRunFinished(active?.currentState),
			mode,
		});

		this.post({ type: 'toolCall', id: 'intent', name: `Intent: ${intent.intent}`, status: 'done', detail: intent.reason });
		if (active) this.postActiveRun(active);

		// Control / query intents — never create a new objective
		switch (intent.intent) {
			case 'STATUS_QUERY':
				await this.replyWithStatusQuery();
				return;
			case 'REQUEST_DIFF':
				await this.replyWithDiffStatus();
				return;
			case 'REQUEST_TESTS':
				await this.replyWithTestStatus();
				return;
			case 'PAUSE':
				await this.handleRunControl('pause');
				return;
			case 'RESUME':
				await this.handleRunControl('resume');
				return;
			case 'CANCEL':
				await this.handleRunControl('cancel');
				return;
			case 'APPROVE':
				await this.handleRunControl('approve');
				return;
			case 'REJECT':
				await this.handleRunControl('reject');
				return;
			case 'CREATE_OBJECTIVE':
				if (mode === 'agent' || mode === 'debug' || mode === 'architect') {
					// No "Scanning workspace: Ready" here — no repository tool call has
					// run yet at this point. Real inspection status now comes from the
					// run panel (`runPanelLabel`), driven by the backend's actual
					// discovery events once the cycle executes.
					await this.handleAdmitAndRun(safeText, 'bounded_repair');
					return;
				}
				break;
			case 'UPDATE_OBJECTIVE':
				// Continue via discuss with active_objective_id — do not admit again
				break;
			default:
				break;
		}

		// Discuss path (GENERAL_CHAT / UPDATE / CREATE in chat mode)
		this.activeAbortController = new AbortController();
		const signal = this.activeAbortController.signal;
		const clientMessageId = `msg:${tenantId || 't'}:${this.currentConv.id}:${Date.now()}`;

		this.post({ type: 'thinking', value: true, stage: 'Asking AIX\u2026' });
		this.sendStart = Date.now();

		const attachments = this.pendingAttachments;
		this.pendingAttachments = [];
		this.postAttachments();

		try {
			const body: DiscussRequestOptions = {
				messages: this.currentConv.messages,
				client_message_id: clientMessageId,
				...(this.currentConv.threadId ? { thread_id: this.currentConv.threadId } : {}),
				...(active?.activeObjectiveId ? { active_objective_id: active.activeObjectiveId } : {}),
				...(intent.intent === 'UPDATE_OBJECTIVE' || hasActive ? { escalate_to_planner: false } : {}),
				...(attachments.length ? { attachments } : {}),
			};

			const response = await postDiscuss(orchestratorUrl, accessToken, tenantId, body, signal);
			const latencyMs = Date.now() - this.sendStart;

			if (signal.aborted) return;

			if (response.status === 401 || response.status === 403 || response.status === 502 || response.status === 503 || response.status >= 400) {
				if (response.status === 401) {
					this.post({ type: 'assistantMessage', text: '', error: { title: 'Authentication Required', status: 401, causes: ['Session expired'], actions: ['Sign In to AIX', 'Open Settings'] } });
				} else if (response.status >= 400) {
					this.post({ type: 'assistantMessage', text: '', error: parseError(response.json as Record<string, unknown>, response.status) });
				}
				return;
			}

			const reply = response.json.reply ?? '(no reply)';
			this.currentConv.messages.push({ role: 'assistant', content: reply });
			this.currentConv.threadId = response.json.thread_id ?? this.currentConv.threadId;
			this.currentConv.preview = trunc(reply, 80);
			this.totalTokens += Math.ceil((safeText.length + reply.length) / 4);

			this.post({ type: 'assistantMessage', text: reply });
			this.post({ type: 'statsUpdated', model: this.modelName(), latencyMs, contextTokens: this.totalTokens });

			const history = this.getHistory().filter(h => h.id !== this.currentConv!.id);
			history.push(this.currentConv);
			await this.saveHistory(history);
			await this.postHistory();

			// Only CREATE_OBJECTIVE may admit — and only when discuss proposes a task and user is in agent-capable mode
			const taskClass = response.json.task_class;
			const proposedScope = response.json.proposed_scope;
			const backendIntent = String(response.json.intent || '');
			const wantsCreate = intent.intent === 'CREATE_OBJECTIVE' || backendIntent === 'objective';
			if (wantsCreate && !hasActive && (taskClass || proposedScope) && (mode === 'agent' || mode === 'architect')) {
				await this.handleAdmitAndRun(safeText, taskClass, proposedScope);
			}

		} catch (err) {
			if (signal.aborted) return;
			const msg = err instanceof Error ? err.message : String(err);
			this.post({
				type: 'assistantMessage', text: '', error: {
					title: 'Connection Failed',
					causes: ['Could not reach AIX at ' + orchestratorUrl, msg],
					actions: ['Verify AIX-Frontend is running on :3000', 'Check aix.orchestratorUrl in Settings'],
				},
			});
		} finally {
			this.post({ type: 'thinking', value: false });
			this.activeAbortController = undefined;
		}
	}

	/* ── HTML rendering ───────────────────────────────────────────────────────── */

	private renderHtml(): string {
		const CSS = `
:root {
  --o: #FF7A1A;
  --o-dim: rgba(255,122,26,.12);
  --o-hover: rgba(255,122,26,.18);
  --green: #4ec9b0;
  --red: #f48771;
  --yellow: #dcdcaa;
  --r: 6px;
  --anim: 150ms ease;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--vscode-font-family);font-size:var(--vscode-font-size,13px);color:var(--vscode-foreground);background:var(--vscode-sideBar-background);height:100vh;overflow:hidden;display:flex;flex-direction:column}

/* ── Header ── */
#hdr{display:flex;align-items:center;justify-content:space-between;padding:0 10px;height:40px;border-bottom:1px solid var(--vscode-sideBarSectionHeader-border,rgba(255,255,255,.06));flex-shrink:0;gap:8px}
.hl{display:flex;align-items:center;gap:7px;min-width:0;overflow:hidden}
.logo{font-weight:700;font-size:13px;color:var(--o);letter-spacing:.5px;flex-shrink:0}
.ws{font-size:11px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;transition:background var(--anim)}
.dot.on{background:var(--green)}.dot.syn{background:var(--yellow);animation:pulse 1s infinite}.dot.off{background:var(--red)}.dot.init{background:var(--vscode-foreground);opacity:.3}
.hr{display:flex;align-items:center;gap:6px;flex-shrink:0}
.mbadge{font-size:10px;padding:2px 7px;border-radius:10px;background:var(--o-dim);color:var(--o);white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis}
.ibtn{width:26px;height:26px;display:flex;align-items:center;justify-content:center;border:none;background:none;color:var(--vscode-foreground);opacity:.55;cursor:pointer;border-radius:var(--r);transition:opacity var(--anim),background var(--anim);font-size:14px;flex-shrink:0}
.ibtn:hover{opacity:1;background:var(--vscode-toolbar-hoverBackground,rgba(255,255,255,.07))}
.ibtn.act{opacity:1;color:var(--o);background:var(--o-dim)}

/* ── History drawer ── */
#hd{position:absolute;top:40px;left:0;right:0;bottom:0;background:var(--vscode-sideBar-background);z-index:20;display:flex;flex-direction:column;transform:translateX(-100%);transition:transform var(--anim)}
#hd.open{transform:translateX(0)}
.hdtop{padding:8px 10px;display:flex;gap:6px;border-bottom:1px solid var(--vscode-sideBarSectionHeader-border,rgba(255,255,255,.06));flex-shrink:0}
.srch{flex:1;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border,transparent);border-radius:var(--r);padding:4px 8px;font-size:12px;font-family:inherit;outline:none}
.srch:focus{border-color:var(--o)}
.ncbtn{padding:4px 10px;background:var(--o);color:#fff;border:none;border-radius:var(--r);font-size:12px;font-family:inherit;cursor:pointer;white-space:nowrap}
.ncbtn:hover{opacity:.85}
.hdbody{flex:1;overflow-y:auto;padding:4px 0}
.hgrp{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;opacity:.35;padding:10px 12px 4px}
.hi{display:flex;align-items:flex-start;gap:6px;padding:6px 12px;cursor:pointer;transition:background var(--anim)}
.hi:hover{background:var(--vscode-list-hoverBackground)}
.hi.cur{background:var(--vscode-list-activeSelectionBackground);color:var(--vscode-list-activeSelectionForeground)}
.hic{flex:1;min-width:0}
.hit{font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:1px}
.hip{font-size:11px;opacity:.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.htime{font-size:10px;opacity:.35;flex-shrink:0;margin-top:2px}
.hia{display:none;gap:2px}
.hi:hover .hia{display:flex}
.hempty{padding:24px 14px;text-align:center;opacity:.4;font-size:12px;line-height:1.6}

/* ── Conversation ── */
#conv{flex:1;overflow-y:auto;scroll-behavior:smooth;position:relative}

/* ── Empty state ── */
#es{display:flex;flex-direction:column;align-items:center;padding:28px 14px 16px}
.es-h{font-size:15px;font-weight:600;margin-bottom:3px;text-align:center}
.es-s{font-size:12px;opacity:.45;text-align:center;margin-bottom:18px;line-height:1.5}
.qa-g{display:grid;grid-template-columns:1fr 1fr;gap:6px;width:100%}
.qa{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:9px 10px;background:var(--vscode-editor-background,var(--vscode-sideBar-background));border:1px solid var(--vscode-sideBarSectionHeader-border,rgba(255,255,255,.08));border-radius:var(--r);cursor:pointer;text-align:left;transition:border-color var(--anim),background var(--anim);color:var(--vscode-foreground);font-family:inherit}
.qa:hover{border-color:var(--o);background:var(--o-dim)}
.qa-i{font-size:15px;margin-bottom:1px}
.qa-l{font-size:11px;font-weight:600}
.qa-s{font-size:10px;opacity:.45}
.signin-wrap{display:flex;flex-direction:column;align-items:center;gap:10px;width:100%;padding:8px 0}
.signin-p{font-size:12px;opacity:.5;text-align:center}
.signin-b{padding:7px 20px;background:var(--o);color:#fff;border:none;border-radius:var(--r);font-size:12px;font-family:inherit;cursor:pointer}
.signin-b:hover{opacity:.85}

/* ── Messages ── */
.mg{padding:12px 14px;border-bottom:1px solid rgba(255,255,255,.04);animation:fadeup 120ms ease}
.mg:last-child{border-bottom:none}
.umsg{display:flex;justify-content:flex-end;gap:7px;align-items:flex-start;margin-bottom:10px}
.utxt{background:var(--vscode-badge-background,rgba(255,255,255,.1));color:var(--vscode-badge-foreground);border-radius:var(--r);padding:6px 10px;font-size:12px;line-height:1.5;max-width:86%;white-space:pre-wrap;word-break:break-word}
.uav{width:20px;height:20px;border-radius:50%;background:var(--o);color:#fff;font-size:9px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}

/* ── Tool timeline ── */
.tl{margin:0 0 6px}
.ts{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:11px;color:var(--vscode-foreground);opacity:.75}
.tsi{width:14px;height:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:11px}
.tsi.run{animation:spin .8s linear infinite;display:inline-block;opacity:.6}
.tsi.ok{color:var(--green)}
.tsi.err{color:var(--red)}
.tsd{font-size:10px;opacity:.4;margin-left:auto}
.tline{width:1px;background:rgba(255,255,255,.07);height:7px;margin:0 6px}

/* ── Thinking ── */
.thk{display:flex;align-items:center;gap:8px;padding:10px 14px;font-size:12px;opacity:.55}
.tdots span{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--o);animation:bounce 1.1s infinite;margin:0 1px}
.tdots span:nth-child(2){animation-delay:.18s}
.tdots span:nth-child(3){animation-delay:.36s}

/* ── Assistant message ── */
.am{font-size:13px;line-height:1.65;color:var(--vscode-foreground)}
.am p{margin:0 0 8px}
.am p:last-child{margin-bottom:0}
.am h1{font-size:14px;font-weight:700;color:var(--o);margin:14px 0 6px}
.am h2{font-size:13px;font-weight:700;margin:12px 0 5px}
.am h3{font-size:13px;font-weight:600;opacity:.85;margin:10px 0 4px}
.am ul,.am ol{padding-left:18px;margin:6px 0}
.am li{margin-bottom:3px}
.am strong{font-weight:700}
.am em{font-style:italic;opacity:.85}
.am code{font-family:var(--vscode-editor-font-family,monospace);font-size:11.5px;background:var(--vscode-textCodeBlock-background,rgba(255,255,255,.06));border-radius:3px;padding:1px 5px;color:var(--yellow)}
.am blockquote{border-left:3px solid var(--o-dim);padding-left:10px;margin:8px 0;opacity:.7}
.am hr{border:none;border-top:1px solid rgba(255,255,255,.08);margin:12px 0}
.am table{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0}
.am th,.am td{border:1px solid rgba(255,255,255,.1);padding:4px 8px;text-align:left}
.am th{background:rgba(255,255,255,.05);font-weight:600}
.am a{color:var(--o);text-decoration:none}
.am a:hover{text-decoration:underline}

/* ── Code block ── */
.cb{margin:8px 0;border-radius:var(--r);overflow:hidden;background:var(--vscode-editor-background,#1e1e1e);border:1px solid rgba(255,255,255,.07)}
.cbh{display:flex;align-items:center;justify-content:space-between;padding:4px 10px;background:rgba(255,255,255,.04);font-size:10px;font-family:var(--vscode-editor-font-family,monospace);opacity:.7}
.cba{display:flex;gap:4px}
.cbb{padding:2px 7px;font-size:10px;border:1px solid rgba(255,255,255,.12);border-radius:3px;background:none;color:var(--vscode-foreground);cursor:pointer;font-family:inherit;opacity:.7;transition:opacity var(--anim),border-color var(--anim)}
.cbb:hover{opacity:1;border-color:var(--o)}
.cb pre{padding:10px 12px;overflow-x:auto;font-family:var(--vscode-editor-font-family,'Cascadia Code',monospace);font-size:12px;line-height:1.55;color:var(--vscode-editor-foreground,#d4d4d4);white-space:pre;margin:0}

/* ── Diff block ── */
.db{margin:8px 0;border-radius:var(--r);overflow:hidden;border:1px solid rgba(255,255,255,.07);font-family:var(--vscode-editor-font-family,monospace);font-size:12px}
.dbh{display:flex;align-items:center;justify-content:space-between;padding:5px 10px;background:rgba(255,255,255,.04);font-size:11px}
.dl{padding:1px 10px;line-height:1.55;white-space:pre}
.dl.add{background:rgba(78,201,176,.1);color:#4ec9b0}
.dl.rem{background:rgba(244,135,113,.1);color:#f48771}
.dl.ctx{opacity:.55}
.db-acts{display:flex;gap:5px;padding:6px 10px;background:rgba(0,0,0,.15)}

/* ── Error card ── */
.ec{border:1px solid rgba(244,135,113,.22);border-radius:var(--r);overflow:hidden;margin:2px 0}
.ech{display:flex;align-items:center;gap:8px;padding:9px 12px;background:rgba(244,135,113,.07)}
.eci{color:var(--red);font-size:14px;flex-shrink:0}
.ect{font-weight:600;font-size:12px;color:var(--red);flex:1;min-width:0}
.ecs{font-size:10px;opacity:.45;flex-shrink:0}
.ecb{padding:10px 12px}
.ecl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;opacity:.4;margin:10px 0 4px}
.ecl:first-child{margin-top:0}
.ecc{font-size:12px;padding:2px 0;display:flex;align-items:flex-start;gap:6px;line-height:1.5}
.ecc::before{content:'→';opacity:.35;flex-shrink:0}
.eca{display:flex;flex-wrap:wrap;gap:5px;margin-top:10px}
.ecab{padding:4px 10px;font-size:11px;border:1px solid rgba(244,135,113,.3);border-radius:4px;background:none;color:var(--red);cursor:pointer;font-family:inherit;transition:background var(--anim)}
.ecab:hover{background:rgba(244,135,113,.1)}
.ecr{font-size:10px;opacity:.35;cursor:pointer;display:flex;align-items:center;gap:4px;margin-top:8px;user-select:none}
.ecrb{display:none;margin-top:6px;padding:6px 8px;background:rgba(0,0,0,.2);border-radius:4px;font-family:monospace;font-size:10px;word-break:break-all;overflow-x:auto;max-height:120px;opacity:.7}
.ecorr{font-size:10px;opacity:.35;margin-top:6px;font-family:monospace}

/* ── Config error ── */
.cerr{margin:10px 14px;padding:10px 12px;background:var(--o-dim);border:1px solid rgba(255,122,26,.25);border-radius:var(--r);font-size:12px;line-height:1.55}
.cerr strong{color:var(--o)}
.cerr code{font-family:monospace;background:rgba(255,122,26,.15);padding:1px 4px;border-radius:3px}

/* ── Context bar ── */
#ctx{display:none;flex-wrap:wrap;gap:4px;padding:5px 10px;border-top:1px solid rgba(255,255,255,.05)}
#ctx.has{display:flex}
.chip{display:flex;align-items:center;gap:3px;padding:2px 6px 2px 8px;background:var(--vscode-badge-background,rgba(255,255,255,.08));border-radius:10px;font-size:11px;max-width:150px}
.chip-l{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.chip-x{width:14px;height:14px;display:flex;align-items:center;justify-content:center;border:none;background:none;color:inherit;cursor:pointer;opacity:.45;font-size:10px;border-radius:50%;flex-shrink:0;transition:opacity var(--anim)}
.chip-x:hover{opacity:1}

/* ── Active run panel ── */
#runbar{display:none;flex-direction:column;gap:6px;padding:8px 10px;border-top:1px solid rgba(255,122,26,.3);background:linear-gradient(180deg,rgba(255,122,26,.1),transparent);flex-shrink:0}
#runbar.has{display:flex}
.run-h{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.run-t{font-size:11px;font-weight:700;color:var(--o);flex:1;min-width:0}
.cv{font-size:9px;opacity:.6;transition:transform var(--anim);flex-shrink:0;width:9px;text-align:center}
#runbar.col .cv,#chbar.col .cv{transform:rotate(-90deg)}
#runbar.col .run-body,#chbar.col .ch-body{display:none}
.run-body,.ch-body{display:flex;flex-direction:column;gap:6px}
.run-st{font-size:10px;opacity:.7;font-family:var(--vscode-editor-font-family,monospace)}
.run-sum{font-size:11px;opacity:.8;line-height:1.4;max-height:32px;overflow:hidden}
.run-plan{font-size:11px;line-height:1.55;white-space:pre-wrap;opacity:.9;max-height:90px;overflow-y:auto}
.run-meta{font-size:10px;opacity:.55}
.run-acts{display:flex;flex-wrap:wrap;gap:4px}
.run-acts button{padding:3px 8px;font-size:10px;border-radius:4px;border:1px solid rgba(255,255,255,.12);background:none;color:var(--vscode-foreground);cursor:pointer;font-family:inherit}
.run-acts button:hover{border-color:var(--o);color:var(--o)}
.run-acts button.danger{border-color:rgba(244,135,113,.35);color:var(--red)}

/* ── Live changes bar (Cursor-like) ── */
#chbar{display:none;flex-direction:column;gap:6px;padding:8px 10px;border-top:1px solid rgba(255,122,26,.25);background:linear-gradient(180deg,rgba(255,122,26,.08),transparent);flex-shrink:0}
#chbar.has{display:flex}
.chhd{display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.cht{font-size:11px;font-weight:600;color:var(--o);flex:1;min-width:0}
.chacts{display:flex;gap:4px}
.chb{padding:3px 8px;font-size:10px;border-radius:4px;border:1px solid rgba(255,255,255,.12);background:none;color:var(--vscode-foreground);cursor:pointer;font-family:inherit}
.chb:hover{border-color:var(--o);color:var(--o)}
.chb.keep{border-color:rgba(78,201,176,.35);color:var(--green)}
.chb.undo{border-color:rgba(244,135,113,.35);color:var(--red)}
.chlist{display:flex;flex-direction:column;gap:3px;max-height:120px;overflow-y:auto}
.chi{display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;cursor:pointer;font-size:11px;transition:background var(--anim)}
.chi:hover,.chi.act{background:var(--o-dim)}
.chi-n{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chi-s{font-family:var(--vscode-editor-font-family,monospace);font-size:10px;opacity:.7;flex-shrink:0}
.chi-s .add{color:var(--green)}.chi-s .rem{color:var(--red)}
.chi-tag{font-size:9px;padding:1px 5px;border-radius:8px;background:rgba(255,255,255,.08);opacity:.7;flex-shrink:0}
.chi-tag.editing{color:var(--o);background:var(--o-dim);opacity:1;animation:pulse 1.2s infinite}
.chi-x{display:flex;gap:2px;flex-shrink:0}
.chi-x button{padding:2px 5px;font-size:9px;border-radius:3px;border:1px solid rgba(255,255,255,.1);background:none;color:inherit;cursor:pointer;font-family:inherit;opacity:.7}
.chi-x button:hover{opacity:1;border-color:var(--o)}

/* ── Composer ── */
#comp{border-top:1px solid rgba(255,255,255,.06);display:flex;flex-direction:column;flex-shrink:0;position:relative}
.mpills{display:flex;gap:2px;padding:7px 10px 0;overflow-x:auto;scrollbar-width:none}
.mpills::-webkit-scrollbar{display:none}
.mp{padding:3px 8px;border-radius:10px;font-size:11px;border:none;background:none;color:var(--vscode-foreground);opacity:.4;cursor:pointer;white-space:nowrap;font-family:inherit;transition:opacity var(--anim),background var(--anim),color var(--anim)}
.mp:hover{opacity:.75;background:rgba(255,255,255,.05)}
.mp.act{opacity:1;background:var(--o-dim);color:var(--o)}
.atch-row{display:flex;flex-wrap:wrap;gap:4px;padding:5px 10px 0}
.ac{display:flex;align-items:center;gap:4px;padding:2px 6px;background:var(--vscode-badge-background,rgba(255,255,255,.08));border-radius:4px;font-size:11px}
.ac-x{background:none;border:none;color:inherit;cursor:pointer;opacity:.45;font-size:10px;padding:0;line-height:1;transition:opacity var(--anim)}
.ac-x:hover{opacity:1}
.irow{display:flex;gap:5px;padding:7px 8px;align-items:flex-end;position:relative}
#inp{flex:1;resize:none;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border,transparent);border-radius:var(--r);padding:7px 10px;font-family:inherit;font-size:13px;min-height:36px;max-height:180px;line-height:1.45;outline:none;overflow-y:auto;transition:border-color var(--anim)}
#inp:focus{border-color:var(--o)}
#inp::placeholder{opacity:.35}
.abtn{width:32px;height:32px;display:flex;align-items:center;justify-content:center;border:1px solid var(--vscode-input-border,transparent);background:var(--vscode-input-background);color:var(--vscode-foreground);border-radius:var(--r);cursor:pointer;opacity:.5;font-size:16px;flex-shrink:0;transition:opacity var(--anim)}
.abtn:hover{opacity:1}
.sbtn{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:var(--o);color:#fff;border:none;border-radius:var(--r);cursor:pointer;font-size:16px;flex-shrink:0;transition:opacity var(--anim)}
.sbtn:hover{opacity:.85}
.sbtn:disabled{opacity:.3;cursor:default}
.stopbtn{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:var(--red,#f48771);color:#fff;border:none;border-radius:var(--r);cursor:pointer;font-size:14px;flex-shrink:0;transition:opacity var(--anim)}
.stopbtn:hover{opacity:.85}

/* ── Slash menu ── */
#sm{position:absolute;bottom:100%;left:8px;right:8px;background:var(--vscode-editorSuggestWidget-background,var(--vscode-editor-background,#1e1e1e));border:1px solid var(--vscode-editorSuggestWidget-border,rgba(255,255,255,.1));border-radius:var(--r);max-height:210px;overflow-y:auto;z-index:50;display:none;box-shadow:0 4px 20px rgba(0,0,0,.35)}
#sm.vis{display:block}
.si{display:flex;align-items:center;gap:8px;padding:6px 10px;cursor:pointer;font-size:12px;transition:background var(--anim)}
.si:hover,.si.sel{background:var(--vscode-list-hoverBackground,rgba(255,255,255,.07))}
.si.sel{background:var(--vscode-list-activeSelectionBackground,rgba(255,122,26,.12))}
.sc{color:var(--o);font-weight:700;min-width:90px;font-family:var(--vscode-editor-font-family,monospace);font-size:12px}
.sd{opacity:.5;font-size:11px}

/* ── Footer ── */
#ft{height:22px;display:flex;align-items:center;gap:8px;padding:0 10px;border-top:1px solid rgba(255,255,255,.05);flex-shrink:0;opacity:.4;font-size:10px}
.fs{opacity:.25}

/* ── Animations ── */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes bounce{0%,60%,100%{transform:translateY(0)}30%{transform:translateY(-3px)}}
@keyframes fadeup{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}
`;

		const HTML = `
<div id="hd">
  <div class="hdtop">
    <input class="srch" id="srch" type="search" placeholder="Search conversations\u2026" autocomplete="off">
    <button class="ncbtn" id="ncbtn">+ New</button>
  </div>
  <div class="hdbody" id="hdbody"></div>
</div>
<div id="hdr">
  <div class="hl">
    <button class="ibtn" id="htog" title="Conversations">&#9776;</button>
    <span class="logo">AIX</span>
    <span class="ws" id="wsname">&mdash;</span>
    <span class="dot init" id="cdot" title="Initializing"></span>
  </div>
  <div class="hr">
    <span class="mbadge" id="mbadge">aix</span>
    <button class="ibtn" id="setbtn" title="Settings">&#9881;</button>
  </div>
</div>
<div id="conv" role="log" aria-live="polite">
  <div id="es">
    <div class="es-h">What do you want to build?</div>
    <div class="es-s">Ask AIX to understand, edit, review,<br>or generate code without leaving VS Code.</div>
    <div class="qa-g" id="qag">
      <button class="qa" data-qa="explain"><span class="qa-i">&#128269;</span><span class="qa-l">Explain Code</span><span class="qa-s">Understand selection</span></button>
      <button class="qa" data-qa="fix"><span class="qa-i">&#128295;</span><span class="qa-l">Fix Error</span><span class="qa-s">Debug an issue</span></button>
      <button class="qa" data-qa="review"><span class="qa-i">&#128065;</span><span class="qa-l">Review Code</span><span class="qa-s">Find problems</span></button>
      <button class="qa" data-qa="test"><span class="qa-i">&#129514;</span><span class="qa-l">Generate Tests</span><span class="qa-s">Full coverage</span></button>
      <button class="qa" data-qa="security"><span class="qa-i">&#128737;</span><span class="qa-l">Security Audit</span><span class="qa-s">Find vulnerabilities</span></button>
      <button class="qa" data-qa="architecture"><span class="qa-i">&#127959;</span><span class="qa-l">Architecture</span><span class="qa-s">Plan systems</span></button>
      <button class="qa" data-qa="docs"><span class="qa-i">&#128196;</span><span class="qa-l">Generate Docs</span><span class="qa-s">Auto-document</span></button>
      <button class="qa" data-qa="optimize"><span class="qa-i">&#9889;</span><span class="qa-l">Performance</span><span class="qa-s">Find bottlenecks</span></button>
    </div>
  </div>
</div>
<div id="ctx"></div>
<div id="runbar">
  <div class="run-h" id="runhd" title="Collapse / expand">
    <span class="cv">&#9660;</span>
    <span class="run-t">Active run</span>
    <span class="run-st" id="runst">—</span>
  </div>
  <div class="run-body">
    <div class="run-sum" id="runsum"></div>
    <div class="run-plan" id="runplan"></div>
    <div class="run-meta" id="runmeta"></div>
    <div class="run-acts">
      <button data-run="status">Status</button>
      <button data-run="diff">Diff</button>
      <button data-run="tests">Tests</button>
      <button data-run="approve">Approve</button>
      <button data-run="resume">Resume</button>
      <button data-run="pause">Pause</button>
      <button class="danger" data-run="cancel">Cancel</button>
    </div>
  </div>
</div>
<div id="chbar">
  <div class="chhd" id="chhd" title="Collapse / expand">
    <span class="cv">&#9660;</span>
    <span class="cht" id="chtitle">AIX changes</span>
    <div class="chacts">
      <button class="chb keep" data-ch="keep-all">Keep all</button>
      <button class="chb undo" data-ch="undo-all">Undo all</button>
    </div>
  </div>
  <div class="ch-body">
    <div class="chlist" id="chlist"></div>
  </div>
</div>
<div id="comp">
  <div class="mpills">
    <button class="mp act" data-m="chat">Chat</button>
    <button class="mp" data-m="agent">Agent</button>
    <button class="mp" data-m="review">Review</button>
    <button class="mp" data-m="debug">Debug</button>
    <button class="mp" data-m="architect">Architect</button>
    <button class="mp" data-m="generate">Generate</button>
  </div>
  <div class="atch-row" id="atchrow" style="display:none"></div>
  <div class="irow">
    <div id="sm"></div>
    <button class="abtn" id="abtn" title="Attach file">&#128206;</button>
    <textarea id="inp" placeholder="Message AIX\u2026 (/ for commands)" rows="1" aria-label="Message"></textarea>
    <button class="sbtn" id="sbtn" title="Send (Enter)">&#8593;</button>
    <button class="stopbtn" id="stopbtn" title="Stop execution" style="display:none">&#9632;</button>
  </div>
</div>
<div id="ft">
  <span id="fmodel">aix</span>
  <span class="fs">&middot;</span>
  <span id="flatency">&mdash;</span>
  <span class="fs">&middot;</span>
  <span id="ftokens">&mdash;</span>
</div>
`;

		const JS = `
var vsc = acquireVsCodeApi();
var signedIn = false, uname = '', mode = 'chat', hdOpen = false, sending = false;
var curTimeline = null, thinkEl = null, totalTk = 0, sendTimer = null;
var EMPTY_HTML = '';

var CMDS = [
  {c:'/fix',       d:'Fix an error or bug',             m:'debug'},
  {c:'/explain',   d:'Explain selected code',           m:'chat'},
  {c:'/review',    d:'Review code for issues',          m:'review'},
  {c:'/test',      d:'Generate unit tests',             m:'generate'},
  {c:'/refactor',  d:'Refactor this code',              m:'agent'},
  {c:'/doc',       d:'Generate documentation',          m:'generate'},
  {c:'/commit',    d:'Generate commit message',         m:'chat'},
  {c:'/search',    d:'Search the workspace',            m:'agent'},
  {c:'/debug',     d:'Debug this issue',                m:'debug'},
  {c:'/architect', d:'Plan system architecture',        m:'architect'},
  {c:'/security',  d:'Security audit',                  m:'review'},
  {c:'/perf',      d:'Performance analysis',            m:'review'},
  {c:'/deploy',    d:'Deployment assistance',           m:'agent'},
  {c:'/git',       d:'Git workflow help',               m:'agent'},
  {c:'/api',       d:'API design or analysis',          m:'architect'},
  {c:'/database',  d:'Database query or schema',        m:'architect'},
  {c:'/run',       d:'Execute / run code',              m:'agent'}
];
var smItems = [], smIdx = -1;

function el(id){return document.getElementById(id);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function relf(ts){var d=Date.now()-ts;if(d<60000)return 'now';if(d<3600000)return Math.floor(d/60000)+'m';if(d<86400000)return Math.floor(d/3600000)+'h';return Math.floor(d/86400000)+'d';}
function fmtTk(n){return n>=1000?(n/1000).toFixed(1)+'k':String(n);}
function scroll(){el('conv').scrollTop=el('conv').scrollHeight;}

function unlockSend(){
  sending=false;
  if(sendTimer){clearTimeout(sendTimer);sendTimer=null;}
  if(el('sbtn')){el('sbtn').disabled=false;el('sbtn').style.display='flex';}
  if(el('stopbtn'))el('stopbtn').style.display='none';
}

function lockSend(){
  sending=true;
  if(el('sbtn'))el('sbtn').disabled=true;
  if(sendTimer)clearTimeout(sendTimer);
  sendTimer=setTimeout(function(){
    unlockSend();
    setThink(false);
  },30000);
}

/* ── History ── */
function toggleHD(){
  hdOpen=!hdOpen;
  el('hd').classList.toggle('open',hdOpen);
  el('htog').classList.toggle('act',hdOpen);
}

function renderHistory(items){
  var body=el('hdbody');
  if(!items.length){body.innerHTML='<div class="hempty">No conversations yet.<br>Start a new chat below.</div>';return;}
  var now=Date.now(),grps={pin:[],tod:[],yes:[],wk:[],old:[]};
  items.forEach(function(it){
    if(it.pinned){grps.pin.push(it);return;}
    var a=now-it.timestamp;
    if(a<86400000)grps.tod.push(it);
    else if(a<172800000)grps.yes.push(it);
    else if(a<604800000)grps.wk.push(it);
    else grps.old.push(it);
  });
  var labels={pin:'Pinned',tod:'Today',yes:'Yesterday',wk:'Last Week',old:'Older'};
  var html='';
  ['pin','tod','yes','wk','old'].forEach(function(k){
    if(!grps[k].length)return;
    html+='<div class="hgrp">'+labels[k]+'</div>';
    grps[k].forEach(function(it){
      html+='<div class="hi" data-id="'+esc(it.id)+'" role="button" tabindex="0">'+
        '<div class="hic">'+
        '<div class="hit">'+(it.pinned?'&#128204; ':'')+esc(it.title)+'</div>'+
        '<div class="hip">'+esc(it.preview)+'</div>'+
        '</div>'+
        '<div class="htime">'+relf(it.timestamp)+'</div>'+
        '<div class="hia">'+
        '<button class="ibtn" data-act="pin" data-id="'+esc(it.id)+'" data-p="'+(it.pinned?1:0)+'" title="'+(it.pinned?'Unpin':'Pin')+'">&#128204;</button>'+
        '<button class="ibtn" data-act="del" data-id="'+esc(it.id)+'" title="Delete">&#128465;</button>'+
        '</div>'+
        '</div>';
    });
  });
  body.innerHTML=html;
}

/* ── Conversation ── */
function clearConv(){
  curTimeline=null;thinkEl=null;unlockSend();
  var c=el('conv');c.innerHTML='';
  var d=document.createElement('div');d.id='es';d.innerHTML=EMPTY_HTML;c.appendChild(d);scroll();
}

function showChat(){
  var e=el('es');
  if(e)e.style.display='none';
}

function addUserMsg(text){
  showChat();
  var g=document.createElement('div');
  g.className='mg';
  g.innerHTML='<div class="umsg"><div class="utxt">'+esc(text)+'</div>'+
    '<div class="uav">'+(uname?uname[0].toUpperCase():'U')+'</div></div>'+
    '<div class="tl"></div>';
  el('conv').appendChild(g);
  curTimeline=g.querySelector('.tl');
  scroll();
}

function setThink(on,stage){
  if(on){
    if(el('sbtn'))el('sbtn').style.display='none';
    if(el('stopbtn'))el('stopbtn').style.display='flex';
    if(!thinkEl){
      thinkEl=document.createElement('div');
      thinkEl.className='thk';
      thinkEl.innerHTML='<div class="tdots"><span></span><span></span><span></span></div>'+
        '<span id="thkstage">'+esc(stage||'Thinking\u2026')+'</span>';
      el('conv').appendChild(thinkEl);scroll();
    }else{
      var s=thinkEl.querySelector('#thkstage');
      if(s)s.textContent=stage||'Thinking\u2026';
    }
  }else{
    if(el('stopbtn'))el('stopbtn').style.display='none';
    if(el('sbtn')){el('sbtn').style.display='flex';el('sbtn').disabled=false;}
    if(thinkEl){thinkEl.remove();thinkEl=null;}
  }
}

function addToolCall(id,name,status,detail){
  if(!curTimeline)return;
  var existing=curTimeline.querySelector('[data-tid="'+id+'"]');
  if(!existing){
    if(curTimeline.children.length>0){
      var ln=document.createElement('div');
      ln.className='tline';
      curTimeline.appendChild(ln);
    }
    existing=document.createElement('div');
    existing.className='ts';
    existing.dataset.tid=id;
    curTimeline.appendChild(existing);
  }
  var icon=status==='run'?'&#9711;':status==='ok'?'&#10003;':'&#10007;';
  var cls=status==='run'?'run':status==='ok'?'ok':'err';
  existing.innerHTML='<span class="tsi '+cls+'">'+icon+'</span>'+
    '<span>'+esc(name)+'</span>'+
    (detail?'<span class="tsd">'+esc(detail)+'</span>':'');
  scroll();
}

function addAsstMsg(text,error){
  showChat();
  if(thinkEl){thinkEl.remove();thinkEl=null;}
  var g=document.createElement('div');
  g.className='mg';
  if(error){
    g.appendChild(buildErrCard(error));
  }else if(text){
    var d=document.createElement('div');
    d.className='am';
    d.innerHTML=renderMD(text);
    if(text.indexOf('Create & run with AIX')!==-1||text.indexOf('Task class:')!==-1||text.indexOf('proposed_scope')!==-1){
      var btnDiv=document.createElement('div');
      btnDiv.style.marginTop='12px';
      var runBtn=document.createElement('button');
      runBtn.className='ncbtn admit-run';
      runBtn.style.cssText='padding:7px 16px;font-size:12px;font-weight:600;background:var(--o);color:#fff;border:none;border-radius:var(--r);cursor:pointer';
      runBtn.textContent='\\uD83D\\uDE80 Create & run with AIX';
      btnDiv.appendChild(runBtn);
      d.appendChild(btnDiv);
    }
    g.appendChild(d);
  }
  el('conv').appendChild(g);
  scroll();
}

function addCfgErr(msg){
  showChat();
  if(thinkEl){thinkEl.remove();thinkEl=null;}
  var d=document.createElement('div');
  d.className='cerr';
  d.innerHTML=renderMD(msg);
  el('conv').appendChild(d);
  scroll();
}

/* ── Error card ── */
function buildErrCard(e){
  var div=document.createElement('div');
  div.className='ec';
  var statusHtml=e.status?'<span class="ecs">HTTP '+e.status+'</span>':'';
  var causesHtml=(e.causes||[]).map(function(c){return '<div class="ecc">'+esc(c)+'</div>';}).join('');
  var actsHtml=(e.actions||[]).map(function(a){return '<button class="ecab" data-err-act="'+esc(a)+'">'+esc(a)+'</button>';}).join('');
  var corrHtml=e.correlationId?'<div class="ecorr">Correlation: '+esc(e.correlationId)+'</div>':'';
  var rawHtml=e.raw?'<div class="ecr" data-toggle-raw="1">&#9656; Technical Details</div><div class="ecrb">'+esc(e.raw)+'</div>':'';
  div.innerHTML='<div class="ech"><span class="eci">&#9888;</span><span class="ect">'+esc(e.title)+'</span>'+statusHtml+'</div>'+
    '<div class="ecb">'+
    (causesHtml?'<div class="ecl">Likely Causes</div>'+causesHtml:'')+
    (actsHtml?'<div class="ecl">Actions</div><div class="eca">'+actsHtml+'</div>':'')+
    corrHtml+rawHtml+
    '</div>';
  return div;
}

/* ── Markdown renderer ── */
function renderMD(text){
  var lines=text.split('\\n'),i=0,html='';
  while(i<lines.length){
    var ln=lines[i];
    if(ln.startsWith('\`\`\`')){
      var lang=ln.slice(3).trim()||'text',code=[]; i++;
      while(i<lines.length&&!lines[i].startsWith('\`\`\`')){code.push(lines[i]);i++;}
      i++;
      html+='<div class="cb"><div class="cbh"><span>'+esc(lang)+'</span>'+
        '<div class="cba"><button class="cbb" data-act="copy">Copy</button></div></div>'+
        '<pre>'+esc(code.join('\\n'))+'</pre></div>';
      continue;
    }
    if((ln.startsWith('---')||ln.startsWith('+++')||ln.startsWith('@@'))&&i===0){
      var dl=[]; while(i<lines.length&&(lines[i].startsWith('---')||lines[i].startsWith('+++')||lines[i].startsWith('@@')||lines[i].startsWith('+')||lines[i].startsWith('-')||lines[i].startsWith(' '))){dl.push(lines[i]);i++;}
      html+=renderDiff(dl); continue;
    }
    if(ln.startsWith('### ')){html+='<h3>'+inMD(ln.slice(4))+'</h3>';i++;continue;}
    if(ln.startsWith('## ')){ html+='<h2>'+inMD(ln.slice(3))+'</h2>';i++;continue;}
    if(ln.startsWith('# ')){  html+='<h1>'+inMD(ln.slice(2))+'</h1>';i++;continue;}
    if(ln==='---'||ln==='***'){html+='<hr>';i++;continue;}
    if(ln.startsWith('> ')){html+='<blockquote>'+inMD(ln.slice(2))+'</blockquote>';i++;continue;}
    if(ln.startsWith('- ')||ln.startsWith('* ')){
      html+='<ul>'; while(i<lines.length&&(lines[i].startsWith('- ')||lines[i].startsWith('* '))){html+='<li>'+inMD(lines[i].slice(2))+'</li>';i++;}
      html+='</ul>'; continue;
    }
    if(/^\\d+\\.\\s/.test(ln)){
      html+='<ol>'; while(i<lines.length&&/^\\d+\\.\\s/.test(lines[i])){html+='<li>'+inMD(lines[i].replace(/^\\d+\\.\\s/,''))+'</li>';i++;}
      html+='</ol>'; continue;
    }
    if(ln.startsWith('|')&&i+1<lines.length&&lines[i+1].startsWith('|---')){
      var heads=ln.split('|').filter(function(c){return c.trim();});
      html+='<table><tr>'+heads.map(function(c){return '<th>'+inMD(c.trim())+'</th>';}).join('')+'</tr>'; i+=2;
      while(i<lines.length&&lines[i].startsWith('|')){
        var cells=lines[i].split('|').filter(function(c){return c.trim();});
        html+='<tr>'+cells.map(function(c){return '<td>'+inMD(c.trim())+'</td>';}).join('')+'</tr>'; i++;
      }
      html+='</table>'; continue;
    }
    if(ln.trim()===''){i++;continue;}
    var ps=[];
    while(i<lines.length&&lines[i].trim()!==''&&!lines[i].startsWith('#')&&!lines[i].startsWith('\`\`\`')&&!lines[i].startsWith('- ')&&!lines[i].startsWith('* ')&&!/^\\d+\\.\\s/.test(lines[i])&&!lines[i].startsWith('>')&&!lines[i].startsWith('|')&&lines[i]!=='---'){
      ps.push(lines[i]);i++;
    }
    if(ps.length)html+='<p>'+inMD(ps.join(' '))+'</p>';
  }
  return html;
}

function inMD(t){
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>')
    .replace(/\\*([^*]+)\\*/g,'<em>$1</em>')
    .replace(/\`([^\`]+)\`/g,'<code>$1</code>')
    .replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2">$1</a>');
}

function renderDiff(dls){
  var html='<div class="db"><div class="dbh"><span>Patch</span>'+
    '<div class="cba">'+
    '<button class="cbb" data-act="apply" style="color:var(--green);border-color:rgba(78,201,176,.3)">Apply</button>'+
    '<button class="cbb" data-act="discard">Discard</button>'+
    '</div></div>';
  dls.forEach(function(l){
    if(l.startsWith('+')&&!l.startsWith('+++'))html+='<div class="dl add">+'+esc(l.slice(1))+'</div>';
    else if(l.startsWith('-')&&!l.startsWith('---'))html+='<div class="dl rem">-'+esc(l.slice(1))+'</div>';
    else html+='<div class="dl ctx">'+esc(l)+'</div>';
  });
  return html+'</div>';
}

function cpCode(btn){
  var code=btn.closest('.cb').querySelector('pre').textContent;
  if(navigator.clipboard&&navigator.clipboard.writeText){
    navigator.clipboard.writeText(code).then(function(){btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy';},1500);});
  }else{var t=document.createElement('textarea');t.value=code;document.body.appendChild(t);t.select();document.execCommand('copy');document.body.removeChild(t);btn.textContent='Copied!';setTimeout(function(){btn.textContent='Copy';},1500);}
}

function applyPatch(btn){
  var lines=Array.from(btn.closest('.db').querySelectorAll('.dl.add')).map(function(l){return l.textContent.slice(1);}).join('\\n');
  vsc.postMessage({type:'runAction',action:'applyPatch',data:lines});
  btn.textContent='Applied';btn.disabled=true;
}

/* ── Slash menu ── */
function updateSM(){
  var val=el('inp').value;
  if(!val.startsWith('/')){hideSM();return;}
  var q=val.toLowerCase();
  var filtered=CMDS.filter(function(c){return c.c.startsWith(q)||c.d.toLowerCase().includes(q.slice(1));});
  if(!filtered.length){hideSM();return;}
  smItems=filtered; smIdx=0;
  el('sm').innerHTML=filtered.map(function(c,i){
    return '<div class="si'+(i===0?' sel':'')+'" data-i="'+i+'" data-c="'+esc(c.c)+'" data-m="'+esc(c.m)+'">'+
      '<span class="sc">'+esc(c.c)+'</span><span class="sd">'+esc(c.d)+'</span></div>';
  }).join('');
  el('sm').classList.add('vis');
}

function hideSM(){el('sm').classList.remove('vis');smItems=[];smIdx=-1;}

function selSM(cmd,m){
  el('inp').value=cmd+' '; hideSM(); setMode(m); el('inp').focus();
}

function smHighlight(){
  el('sm').querySelectorAll('.si').forEach(function(it,i){
    it.classList.toggle('sel',i===smIdx);
    if(i===smIdx)it.scrollIntoView({block:'nearest'});
  });
}

var PLACEHOLDERS = {
  chat: 'Message AIX\\u2026 (/ for commands)',
  agent: 'Ask AIX Agent to build features, fix bugs, or edit files\\u2026',
  review: 'Ask AIX to review current code or security\\u2026',
  debug: 'Describe an error or bug to debug\\u2026',
  architect: 'Describe system or API architecture to design\\u2026',
  generate: 'Describe code or tests to generate\\u2026'
};

function setMode(m){
  mode=m;
  document.querySelectorAll('.mp').forEach(function(p){
    p.classList.toggle('act',p.dataset.m===m);
  });
  var inp=el('inp');
  if(inp){
    inp.placeholder=PLACEHOLDERS[m]||'Message AIX\\u2026';
    inp.focus();
  }
}

/* ── Attachments ── */
function renderAttach(items){
  var row=el('atchrow');
  if(!items.length){row.style.display='none';row.innerHTML='';return;}
  row.style.display='flex';
  row.innerHTML=items.map(function(a,i){
    return '<div class="ac"><span>&#128196; '+esc(a.filename)+'</span>'+
      '<button class="ac-x" data-rm-atch="'+i+'" title="Remove">&times;</button></div>';
  }).join('');
}

/* ── Live file changes bar ── */
function renderActiveRun(task){
  var bar=el('runbar');
  if(!task||!task.activeCycleId){bar.classList.remove('has');return;}
  bar.classList.add('has');
  el('runst').textContent=task.currentState||'—';
  el('runsum').textContent=task.objectiveSummary||'Working…';
  var plan=(task.plan||[]).map(function(s){
    var m=s.status==='done'?'✓':s.status==='running'?'●':s.status==='error'?'✗':'○';
    return m+' '+s.label;
  }).join('\\n');
  el('runplan').textContent=plan||(task.currentStep||'');
  el('runmeta').textContent=(task.changedFiles&&task.changedFiles.length?task.changedFiles.length+' files changed':'No files yet')+
    (task.testResults&&task.testResults.summary?' · '+task.testResults.summary:'');
}

function renderFileChanges(changes,activePath){
  var bar=el('chbar'), list=el('chlist'), title=el('chtitle');
  var pending=(changes||[]).filter(function(c){return c.status==='pending'||c.status==='editing';});
  if(!pending.length){bar.classList.remove('has');list.innerHTML='';return;}
  bar.classList.add('has');
  var adds=pending.reduce(function(n,c){return n+c.addedLines;},0);
  var rems=pending.reduce(function(n,c){return n+c.removedLines;},0);
  title.textContent='AIX changes · '+pending.length+' file'+(pending.length===1?'':'s')+'  +'+adds+'/−'+rems;
  list.innerHTML=pending.map(function(c){
    var act=activePath&&c.filePath===activePath?' act':'';
    var tag=c.status==='editing'?'<span class="chi-tag editing">editing</span>':(c.isNew?'<span class="chi-tag">new</span>':'');
    return '<div class="chi'+act+'" data-diff="'+esc(c.filePath)+'">'+
      '<span class="chi-n" title="'+esc(c.relativePath)+'">'+esc(c.relativePath)+'</span>'+
      tag+
      '<span class="chi-s"><span class="add">+'+c.addedLines+'</span> <span class="rem">−'+c.removedLines+'</span></span>'+
      '<span class="chi-x">'+
      '<button data-keep="'+esc(c.filePath)+'" title="Keep">Keep</button>'+
      '<button data-undo="'+esc(c.filePath)+'" title="Undo">Undo</button>'+
      '</span></div>';
  }).join('');
}

/* ── Input & send ── */
function doSend(){
  var txt=el('inp').value.trim();
  if(!txt||sending)return;
  lockSend();
  addUserMsg(txt);
  el('inp').value=''; el('inp').style.height='auto'; hideSM();
  vsc.postMessage({type:'send',text:txt,mode:mode});
}

/* ── Auth & conn ── */
function setConn(state,title){
  var d=el('cdot'); d.className='dot '+state; d.title=title||'';
}

function updateAuth(s){
  signedIn=s.signedIn; uname=s.username||'';
  if(s.workspace)el('wsname').textContent=s.workspace;
  if(s.signedIn){
    setConn('on','Connected as '+(s.username||'')+(s.tenantId?' \\u00b7 '+s.tenantId:''));
    var qi=el('qag')||document.querySelector('.qa-g');
    if(qi)qi.style.display='';
    var sw=document.querySelector('.signin-wrap');
    if(sw)sw.remove();
  }else{
    setConn('init','Not signed in');
    var qb=el('qag')||document.querySelector('.qa-g');
    if(qb&&!document.querySelector('.signin-wrap')){
      var wrap=document.createElement('div');
      wrap.className='signin-wrap';
      var p=document.createElement('p');
      p.className='signin-p';
      p.textContent='Sign in to start working with AIX.';
      var btn=document.createElement('button');
      btn.className='signin-b';
      btn.textContent='Sign In to AIX';
      wrap.appendChild(p);wrap.appendChild(btn);
      qb.parentNode.insertBefore(wrap,qb);
      qb.style.display='none';
    }
  }
}

/* ── Stats ── */
function updateStats(s){
  if(s.model)el('mbadge').textContent=s.model;
  if(s.model)el('fmodel').textContent=s.model;
  el('flatency').textContent=s.latencyMs?s.latencyMs+'ms':'\\u2014';
  if(s.contextTokens){totalTk=s.contextTokens;el('ftokens').textContent=fmtTk(totalTk)+' tokens';}
}

/* ── Delegated click handler (CSP-safe; no inline onclick) ── */
document.body.addEventListener('click',function(e){
  var t=e.target;
  if(!t||!t.closest)return;
  var node;

  if((node=t.closest('#htog'))){toggleHD();return;}
  if((node=t.closest('#ncbtn'))){vsc.postMessage({type:'newChat'});clearConv();if(hdOpen)toggleHD();return;}
  if((node=t.closest('#setbtn'))){vsc.postMessage({type:'runAction',action:'openSettings'});return;}
  if((node=t.closest('#abtn'))){vsc.postMessage({type:'attach'});return;}
  if((node=t.closest('#sbtn'))){doSend();return;}
  if((node=t.closest('#stopbtn'))){
    vsc.postMessage({type:'cancel'});
    unlockSend();
    if(thinkEl){thinkEl.remove();thinkEl=null;}
    return;
  }
  if((node=t.closest('.mp'))){setMode(node.dataset.m);return;}
  if((node=t.closest('.qa'))){vsc.postMessage({type:'quickAction',action:node.dataset.qa});return;}
  if((node=t.closest('.signin-b'))){vsc.postMessage({type:'signIn'});return;}
  if((node=t.closest('.admit-run'))){vsc.postMessage({type:'admitAndRun'});return;}
  if((node=t.closest('[data-rm-atch]'))){vsc.postMessage({type:'removeAttachment',index:parseInt(node.getAttribute('data-rm-atch'),10)});return;}
  if((node=t.closest('.ecab'))){
    var act=node.getAttribute('data-err-act')||node.textContent||'';
    if(act.indexOf('Sign')!==-1)vsc.postMessage({type:'signIn'});
    else if(act.indexOf('Settings')!==-1)vsc.postMessage({type:'runAction',action:'openSettings'});
    return;
  }
  if((node=t.closest('[data-toggle-raw]'))){
    var rb=node.nextElementSibling;
    if(rb)rb.style.display=rb.style.display==='block'?'none':'block';
    return;
  }
  if((node=t.closest('[data-run]'))){
    var a=node.getAttribute('data-run');
    if(a==='status')vsc.postMessage({type:'send',text:'done?',mode:mode});
    else vsc.postMessage({type:'runControl',action:a});
    return;
  }
  if((node=t.closest('[data-ch="keep-all"]'))){vsc.postMessage({type:'keepEdit'});return;}
  if((node=t.closest('[data-ch="undo-all"]'))){vsc.postMessage({type:'undoEdit'});return;}
  if((node=t.closest('[data-keep]'))){vsc.postMessage({type:'keepEdit',filePath:node.getAttribute('data-keep')});return;}
  if((node=t.closest('[data-undo]'))){vsc.postMessage({type:'undoEdit',filePath:node.getAttribute('data-undo')});return;}
  if((node=t.closest('[data-diff]'))){vsc.postMessage({type:'openDiff',filePath:node.getAttribute('data-diff')});return;}
  if((node=t.closest('#runhd'))){el('runbar').classList.toggle('col');return;}
  if((node=t.closest('#chhd'))){el('chbar').classList.toggle('col');return;}
  if((node=t.closest('.cbb[data-act="copy"]'))){cpCode(node);return;}
  if((node=t.closest('.cbb[data-act="apply"]'))){applyPatch(node);return;}
  if((node=t.closest('.cbb[data-act="discard"]'))){var db=node.closest('.db');if(db)db.remove();return;}
  if((node=t.closest('[data-act="pin"],[data-act="del"]'))){
    e.stopPropagation();
    if(node.dataset.act==='del')vsc.postMessage({type:'deleteConversation',id:node.dataset.id});
    if(node.dataset.act==='pin')vsc.postMessage({type:'pinConversation',id:node.dataset.id,pinned:node.dataset.p!=='1'});
    return;
  }
  if((node=t.closest('.hi'))){
    if(t.closest('.hia'))return;
    vsc.postMessage({type:'loadConversation',id:node.dataset.id});
    if(hdOpen)toggleHD();
    return;
  }
  if((node=t.closest('.si'))){selSM(node.dataset.c,node.dataset.m);return;}
});

document.body.addEventListener('mouseover',function(e){
  var it=e.target&&e.target.closest&&e.target.closest('.si');
  if(!it)return;
  el('sm').querySelectorAll('.si').forEach(function(x){x.classList.remove('sel');});
  it.classList.add('sel'); smIdx=parseInt(it.dataset.i,10);
});

el('srch').addEventListener('input',function(){vsc.postMessage({type:'searchHistory',query:this.value});});
el('inp').addEventListener('input',function(){
  this.style.height='auto';
  this.style.height=Math.min(this.scrollHeight,180)+'px';
  updateSM();
});
el('inp').addEventListener('keydown',function(e){
  if(el('sm').classList.contains('vis')){
    if(e.key==='ArrowDown'){e.preventDefault();smIdx=Math.min(smIdx+1,smItems.length-1);smHighlight();}
    else if(e.key==='ArrowUp'){e.preventDefault();smIdx=Math.max(smIdx-1,0);smHighlight();}
    else if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();var it=smItems[smIdx];if(it)selSM(it.c,it.m);}
    else if(e.key==='Escape')hideSM();
    return;
  }
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();doSend();}
});

/* ── Message handler ── */
window.addEventListener('message',function(e){
  var m=e.data;
  switch(m.type){
    case 'authState':      updateAuth(m); break;
    case 'thinking':       setThink(m.value,m.stage);
                           if(!m.value)unlockSend(); break;
    case 'toolCall':       addToolCall(m.id,m.name,m.status==='running'?'run':m.status==='done'?'ok':'err',m.detail); break;
    case 'assistantMessage': addAsstMsg(m.text,m.error); unlockSend(); break;
    case 'displayUserMessage': addUserMsg(m.text); lockSend(); break;
    case 'attachmentsUpdated': renderAttach(m.attachments); break;
    case 'historyUpdated': renderHistory(m.items); break;
    case 'conversationLoaded':
      el('conv').innerHTML='';
      m.messages.forEach(function(msg){
        if(msg.role==='user')addUserMsg(msg.text);
        else addAsstMsg(msg.text,null);
      });
      setMode(m.mode); unlockSend(); break;
    case 'statsUpdated':   updateStats(m); break;
    case 'configError':    addCfgErr(m.message); unlockSend(); break;
    case 'fileChanges':    renderFileChanges(m.changes,m.activePath); break;
    case 'activeRun':      renderActiveRun(m.task); break;
  }
});

(function init(){
  var esEl=el('es');
  if(esEl)EMPTY_HTML=esEl.innerHTML;
  vsc.postMessage({type:'ready'});
})();
`;

		return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<title>AIX</title>
<style>${CSS}</style>
</head>
<body>
${HTML}
<script>${JS}</script>
</body>
</html>`;
	}
}

/* ── Module helpers ─────────────────────────────────────────────────────────── */

function uid(): string {
	return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

function trunc(s: string, max: number): string {
	s = (s || '').trim();
	return s.length > max ? s.slice(0, max - 1) + '\u2026' : s;
}

function delay(ms: number): Promise<void> {
	return new Promise(r => setTimeout(r, ms));
}

const MIME_BY_EXT: Record<string, string> = {
	'.ts': 'text/typescript', '.js': 'text/javascript', '.py': 'text/x-python',
	'.md': 'text/markdown', '.json': 'application/json', '.txt': 'text/plain',
	'.png': 'image/png', '.jpg': 'image/jpeg', '.pdf': 'application/pdf',
	'.csv': 'text/csv', '.html': 'text/html', '.css': 'text/css',
};

function guessMime(filename: string): string {
	return MIME_BY_EXT[path.extname(filename).toLowerCase()] ?? 'application/octet-stream';
}

function eventKey(ev: CycleEvent): string {
	// A real server-assigned event_id (see events.py's now-wired versioned schema) is
	// authoritative and unique — prefer it. Fall back to the old composite key only
	// for legacy/disk-snapshot events that predate it, so old workspaces don't break.
	if (ev.event_id) return ev.event_id;
	return [ev.event || ev.event_type || 'event', ev.at || ev.occurred_at || '', ev.workspace || ''].join('|');
}

/** Human-readable run-panel label for a real discovery/tool event, matching the
 * required live-panel steps (Inspecting repository / Reading .../ Searching .../
 * Editing real application files / Running validation / Preparing diff). */
function runPanelLabel(ev: CycleEvent): string {
	const evType = String(ev.event_type || ev.event || '');
	const refs = (ev.payload_refs || {}) as Record<string, unknown>;
	switch (evType) {
		case 'discovery.started': return 'Inspecting repository';
		case 'discovery.completed': return 'Repository inspection complete';
		case 'tool.file_read': return `Reading ${refs.relative_path || 'file'}…`;
		case 'tool.list_directory': return `Listing ${refs.relative_path || '.'}…`;
		case 'tool.search': return `Searching ${refs.pattern || 'repository'}…`;
		case 'tool.file_written':
		case 'tool.patch_applied':
		case 'tool.file_deleted': return 'Editing real application files…';
		case 'tool.command_run': return 'Running validation…';
		case 'tool.git_status': return 'Checking repository status…';
		case 'tool.git_diff': return 'Preparing diff…';
		default: return evType || 'Working…';
	}
}

function activityLabelForPhase(phase: string): string {
	switch (phase.toUpperCase()) {
		case 'PLANNING': return 'Planning next steps';
		case 'WAITING_APPROVAL':
		case 'AWAITING_APPROVAL': return 'Waiting for approval';
		case 'QUEUED': return 'Queued for execution';
		case 'RUNNING':
		case 'RUNNING_STEP': return 'Executing implementation step';
		case 'BLOCKED':
		case 'PAUSED':
		case 'WAITING_BLOCKER': return 'Paused / blocked';
		case 'VERIFYING': return 'Running verification';
		case 'COMPLETED': return 'Completed';
		case 'COMPLETED_FOR_REVIEW': return 'Completed — awaiting review';
		case 'FAILED': return 'Failed';
		case 'CANCELLED':
		case 'CANCELED': return 'Cancelled';
		default: return phase;
	}
}

function formatCycleError(json: Record<string, unknown>): string {
	const err = json.error;
	if (err && typeof err === 'object') {
		const e = err as { code?: string; detail?: string };
		return [e.code, e.detail].filter(Boolean).join(': ') || JSON.stringify(err);
	}
	const detail = json.detail;
	if (Array.isArray(detail)) {
		return detail.map(item => {
			if (!item || typeof item !== 'object') return String(item);
			const row = item as { loc?: unknown[]; msg?: string; type?: string; code?: string };
			const loc = Array.isArray(row.loc) ? row.loc.join('.') : '';
			return [loc, row.msg || row.code || row.type].filter(Boolean).join(': ');
		}).filter(Boolean).join('; ') || JSON.stringify(detail);
	}
	if (detail && typeof detail === 'object') {
		const d = detail as { code?: string; detail?: string; message?: string };
		return [d.code, d.detail || d.message].filter(Boolean).join(': ') || JSON.stringify(detail);
	}
	if (typeof detail === 'string' && detail.trim()) return detail;
	return String(json.message || '');
}

function formatPlanMarkdown(steps: PlanStep[]): string {
	return steps.map(s => {
		const mark = s.status === 'done' ? '\u2713' : s.status === 'running' ? '\u25CF' : s.status === 'error' ? '\u2717' : '\u25CB';
		return `${mark} ${s.label}`;
	}).join('\n');
}

function extractPlanSteps(json: Record<string, unknown>, phase: string): PlanStep[] {
	const candidates = (json.plan || json.steps || json.items || json.item) as unknown;
	const arr = Array.isArray(candidates)
		? candidates
		: (candidates && typeof candidates === 'object' && Array.isArray((candidates as { steps?: unknown[] }).steps))
			? (candidates as { steps: unknown[] }).steps
			: [];
	if (!arr.length) return [];
	return arr.map((raw, i) => {
		const s = (raw && typeof raw === 'object') ? raw as Record<string, unknown> : {};
		const label = String(s.label || s.title || s.name || s.step || `Step ${i + 1}`);
		const st = String(s.status || s.state || '').toLowerCase();
		let status: PlanStep['status'] = 'todo';
		if (st.includes('done') || st.includes('complete') || st === 'ok') status = 'done';
		else if (st.includes('run') || st.includes('active') || st.includes('progress')) status = 'running';
		else if (st.includes('fail') || st.includes('error')) status = 'error';
		else if (i === 0 && /RUNNING|VERIFYING|PLANNING/i.test(phase)) status = 'running';
		return { id: String(s.id || i + 1), label, status };
	});
}

function langFor(name: string): string {
	const ext = path.extname(name).toLowerCase();
	if (ext === '.html' || ext === '.htm') return 'html';
	if (ext === '.css') return 'css';
	if (ext === '.json') return 'json';
	if (ext === '.ts') return 'typescript';
	if (ext === '.js') return 'javascript';
	if (ext === '.py') return 'python';
	if (ext === '.md') return 'markdown';
	return 'text';
}

function trimContent(content: string, max = 12000): string {
	if (content.length <= max) return content;
	return content.slice(0, max) + '\n\n/* \u2026 truncated for chat view \u2026 */';
}

function parseError(json: Record<string, unknown>, status: number): StructuredError {
	const detail = json.detail as Record<string, unknown> | string | undefined;
	let code = '', rawDetail = '';

	if (detail && typeof detail === 'object') {
		code = String(detail.code ?? '');
		rawDetail = String(detail.detail ?? '');
	} else if (typeof detail === 'string') {
		rawDetail = detail;
	}

	if (code === 'INTEGRATION_CHAT_REJECTED') {
		const bodyMatch = rawDetail.match(/body=(.+)$/s);
		const statusMatch = rawDetail.match(/status=(\d+)/);
		const upstreamStatus = statusMatch ? Number(statusMatch[1]) : undefined;
		let title = 'Integration Rejected';
		let causes: string[] = ['The request was rejected by hyeve-integration-service'];
		let actions = ['Verify hyeve-integration-service is running and reachable', 'Check aix.integrationUrl / the AIX backend’s RUNTIME_PLANNER_API_URL'];
		if (bodyMatch) {
			try {
				const inner = JSON.parse(bodyMatch[1]);
				title = String(inner.detail ?? inner.title ?? title);
				if (inner.detail === 'Tenant mismatch') {
					causes = [
						'The tenant ID in your session does not match the integration service',
						'You may have signed in with the wrong tenant ID',
					];
					actions = ['Sign Out and Sign In again with the correct tenant', 'Verify aix.tenantId matches your workspace'];
				} else if (upstreamStatus === 404) {
					causes = [
						'hyeve-integration-service responded with 404 for the chat-completion route it should serve',
						'Either the service is not the one actually listening at the configured URL, or it needs a restart to pick up the route',
					];
				} else if (upstreamStatus === 401 || upstreamStatus === 403) {
					causes = ['hyeve-integration-service rejected the forwarded credentials'];
					actions = ['Sign Out and Sign In again with the correct tenant', 'Verify aix.tenantId matches your workspace'];
				}
			} catch { /* use defaults */ }
		}
		const corrMatch = rawDetail.match(/"correlationId"\s*:\s*"([^"]+)"/);
		return {
			title, status,
			correlationId: corrMatch?.[1],
			causes,
			actions,
			raw: rawDetail,
		};
	}

	return {
		title: String(json.message ?? 'Request Failed'),
		status,
		causes: [rawDetail || 'HTTP ' + status],
		actions: ['Check AIX orchestrator logs', 'Verify network connectivity'],
		raw: JSON.stringify(json, null, 2),
	};
}
