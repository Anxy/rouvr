/*---------------------------------------------------------------------------------------------
 *  Cursor-like live edit session: status bar, explorer badges, inline green decorations,
 *  side-by-side diff review, and Keep / Undo for proposed AIX file changes.
 *--------------------------------------------------------------------------------------------*/
import * as fs from 'fs/promises';
import * as path from 'path';
import * as vscode from 'vscode';

export interface FileChangeSummary {
	filePath: string;
	relativePath: string;
	status: 'editing' | 'pending' | 'kept' | 'undone';
	addedLines: number;
	removedLines: number;
	isNew: boolean;
}

interface TrackedEdit {
	uri: vscode.Uri;
	relativePath: string;
	originalContent: string;
	proposedContent: string;
	isNew: boolean;
	status: 'editing' | 'pending' | 'kept' | 'undone';
	addedLines: number;
	removedLines: number;
	decorationIds: string[];
}

type ChangeListener = (changes: FileChangeSummary[], activePath?: string) => void;

export class AixEditSession implements vscode.FileDecorationProvider, vscode.Disposable {
	private readonly tracked = new Map<string, TrackedEdit>();
	private readonly _onDidChangeFileDecorations = new vscode.EventEmitter<vscode.Uri | vscode.Uri[] | undefined>();
	readonly onDidChangeFileDecorations = this._onDidChangeFileDecorations.event;

	private readonly statusBar: vscode.StatusBarItem;
	private readonly addDecorationType: vscode.TextEditorDecorationType;
	private readonly editingDecorationType: vscode.TextEditorDecorationType;
	private readonly listeners = new Set<ChangeListener>();
	private activePath: string | undefined;
	private disposed = false;

	constructor(private readonly context: vscode.ExtensionContext) {
		this.statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
		this.statusBar.command = 'aix.showChanges';
		this.statusBar.tooltip = 'AIX proposed edits — click to review';
		context.subscriptions.push(this.statusBar);

		this.addDecorationType = vscode.window.createTextEditorDecorationType({
			isWholeLine: true,
			backgroundColor: new vscode.ThemeColor('diffEditor.insertedLineBackground'),
			overviewRulerColor: new vscode.ThemeColor('diffEditor.inserted'),
			overviewRulerLane: vscode.OverviewRulerLane.Left,
			borderWidth: '0 0 0 3px',
			borderStyle: 'solid',
			borderColor: new vscode.ThemeColor('diffEditor.insertedTextBorder'),
		});

		this.editingDecorationType = vscode.window.createTextEditorDecorationType({
			isWholeLine: true,
			backgroundColor: new vscode.ThemeColor('editor.wordHighlightStrongBackground'),
			overviewRulerColor: new vscode.ThemeColor('editorOverviewRuler.wordHighlightStrongForeground'),
			overviewRulerLane: vscode.OverviewRulerLane.Center,
		});

		context.subscriptions.push(
			this.addDecorationType,
			this.editingDecorationType,
			vscode.window.registerFileDecorationProvider(this),
			vscode.window.onDidChangeActiveTextEditor(ed => {
				if (ed) this.reapplyDecorations(ed);
			}),
			vscode.workspace.onDidChangeTextDocument(e => {
				const key = e.document.uri.fsPath;
				const t = this.tracked.get(key);
				if (t && t.status === 'pending') {
					// User typed — keep decorations on current content lines that still match additions
					const ed = vscode.window.visibleTextEditors.find(x => x.document.uri.fsPath === key);
					if (ed) this.applyAddDecorations(ed, t.proposedContent);
				}
			}),
		);

		context.subscriptions.push(
			vscode.commands.registerCommand('aix.keepEdit', async (filePath?: string) => {
				if (filePath) await this.keep(filePath);
				else await this.keepAll();
			}),
			vscode.commands.registerCommand('aix.undoEdit', async (filePath?: string) => {
				if (filePath) await this.undo(filePath);
				else await this.undoAll();
			}),
			vscode.commands.registerCommand('aix.openDiff', async (filePath?: string) => {
				if (filePath) await this.openDiff(filePath);
				else {
					const first = [...this.tracked.values()].find(t => t.status === 'pending' || t.status === 'editing');
					if (first) await this.openDiff(first.uri.fsPath);
				}
			}),
			vscode.commands.registerCommand('aix.showChanges', async () => {
				const pending = [...this.tracked.values()].filter(t => t.status === 'pending' || t.status === 'editing');
				if (!pending.length) {
					void vscode.window.showInformationMessage('No pending AIX edits.');
					return;
				}
				const pick = await vscode.window.showQuickPick(
					pending.map(t => ({
						label: t.relativePath,
						description: `${t.isNew ? 'new' : 'modified'}  +${t.addedLines} −${t.removedLines}`,
						filePath: t.uri.fsPath,
					})),
					{ title: 'AIX changes — select a file to review' },
				);
				if (pick) await this.openDiff(pick.filePath);
			}),
		);
	}

	onDidChange(listener: ChangeListener): vscode.Disposable {
		this.listeners.add(listener);
		listener(this.summaries(), this.activePath);
		return { dispose: () => this.listeners.delete(listener) };
	}

	summaries(): FileChangeSummary[] {
		return [...this.tracked.values()].map(t => ({
			filePath: t.uri.fsPath,
			relativePath: t.relativePath,
			status: t.status,
			addedLines: t.addedLines,
			removedLines: t.removedLines,
			isNew: t.isNew,
		}));
	}

	provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
		const t = this.tracked.get(uri.fsPath);
		if (!t || t.status === 'kept' || t.status === 'undone') return undefined;
		if (t.status === 'editing') {
			return new vscode.FileDecoration('✎', 'AIX is editing this file', new vscode.ThemeColor('charts.orange'));
		}
		return new vscode.FileDecoration('●', `AIX pending (+${t.addedLines}/−${t.removedLines})`, new vscode.ThemeColor('gitDecoration.modifiedResourceForeground'));
	}

	/**
	 * Begin editing a file: show "editing" state, open editor, pulse decorations.
	 */
	async beginEdit(targetPath: string, label?: string): Promise<void> {
		const uri = vscode.Uri.file(targetPath);
		const relativePath = vscode.workspace.asRelativePath(uri, false);
		this.activePath = targetPath;

		let originalContent = '';
		let isNew = true;
		try {
			originalContent = await fs.readFile(targetPath, 'utf8');
			isNew = false;
		} catch {
			isNew = true;
		}

		const existing = this.tracked.get(targetPath);
		if (!existing) {
			this.tracked.set(targetPath, {
				uri,
				relativePath,
				originalContent,
				proposedContent: originalContent,
				isNew,
				status: 'editing',
				addedLines: 0,
				removedLines: 0,
				decorationIds: [],
			});
		} else {
			existing.status = 'editing';
			if (!existing.originalContent && originalContent) {
				existing.originalContent = originalContent;
				existing.isNew = false;
			}
		}

		this.statusBar.text = `$(edit) AIX editing ${label || path.basename(targetPath)}`;
		this.statusBar.show();
		this._onDidChangeFileDecorations.fire(uri);
		this.notify();

		try {
			const doc = await vscode.workspace.openTextDocument(uri);
			const editor = await vscode.window.showTextDocument(doc, { preview: false, viewColumn: vscode.ViewColumn.One, preserveFocus: false });
			const lineCount = Math.max(doc.lineCount, 1);
			editor.setDecorations(this.editingDecorationType, [new vscode.Range(0, 0, lineCount - 1, Number.MAX_SAFE_INTEGER)]);
		} catch {
			// file may not exist yet — will appear after write
		}
	}

	/**
	 * Write proposed content, open the file, apply green line decorations, open diff.
	 */
	async applyProposed(
		targetPath: string,
		proposedContent: string,
		opts?: { openDiff?: boolean; reveal?: boolean },
	): Promise<FileChangeSummary> {
		const uri = vscode.Uri.file(targetPath);
		const relativePath = vscode.workspace.asRelativePath(uri, false);
		this.activePath = targetPath;

		let originalContent = '';
		let isNew = true;
		const existing = this.tracked.get(targetPath);
		if (existing) {
			originalContent = existing.originalContent;
			isNew = existing.isNew && !existing.originalContent;
		} else {
			try {
				originalContent = await fs.readFile(targetPath, 'utf8');
				isNew = false;
			} catch {
				isNew = true;
				originalContent = '';
			}
		}

		await fs.mkdir(path.dirname(targetPath), { recursive: true });
		await fs.writeFile(targetPath, proposedContent, 'utf8');

		const { added, removed } = lineStats(originalContent, proposedContent);
		const tracked: TrackedEdit = {
			uri,
			relativePath,
			originalContent,
			proposedContent,
			isNew,
			status: 'pending',
			addedLines: added,
			removedLines: removed,
			decorationIds: [],
		};
		this.tracked.set(targetPath, tracked);

		this.statusBar.text = `$(diff-modified) AIX +${added}/−${removed}  ${path.basename(targetPath)}`;
		this.statusBar.show();
		this._onDidChangeFileDecorations.fire(uri);
		this.notify();

		const doc = await vscode.workspace.openTextDocument(uri);
		await doc.save().catch(() => undefined);
		const editor = await vscode.window.showTextDocument(doc, {
			preview: false,
			viewColumn: vscode.ViewColumn.One,
			preserveFocus: !(opts?.reveal ?? true),
		});

		this.applyAddDecorations(editor, proposedContent);

		// Reveal first changed region
		const firstChangeLine = firstChangedLine(originalContent, proposedContent);
		if (firstChangeLine >= 0) {
			const line = Math.min(firstChangeLine, doc.lineCount - 1);
			editor.revealRange(new vscode.Range(line, 0, line, 0), vscode.TextEditorRevealType.InCenter);
			editor.selection = new vscode.Selection(line, 0, line, 0);
		}

		if (opts?.openDiff !== false) {
			await this.openDiff(targetPath);
		}

		return this.summaries().find(s => s.filePath === targetPath)!;
	}

	async openDiff(filePath: string): Promise<void> {
		const t = this.tracked.get(filePath);
		if (!t) {
			await vscode.window.showTextDocument(vscode.Uri.file(filePath));
			return;
		}

		const left = await this.originalVirtualUri(t);
		const right = t.uri;
		const title = `${t.relativePath} (AIX${t.isNew ? ' · new' : ''})`;
		await vscode.commands.executeCommand('vscode.diff', left, right, title, { preview: true });
	}

	async keep(filePath: string): Promise<void> {
		const t = this.tracked.get(filePath);
		if (!t) return;
		t.status = 'kept';
		this.clearEditorDecorations(filePath);
		this._onDidChangeFileDecorations.fire(t.uri);
		this.refreshStatus();
		this.notify();
	}

	async undo(filePath: string): Promise<void> {
		const t = this.tracked.get(filePath);
		if (!t) return;
		try {
			if (t.isNew && !t.originalContent) {
				await fs.unlink(filePath).catch(() => undefined);
			} else {
				await fs.writeFile(filePath, t.originalContent, 'utf8');
			}
		} catch (err) {
			void vscode.window.showErrorMessage(`AIX undo failed: ${err instanceof Error ? err.message : String(err)}`);
			return;
		}
		t.status = 'undone';
		this.clearEditorDecorations(filePath);
		this._onDidChangeFileDecorations.fire(t.uri);

		// Close editors for deleted new files
		if (t.isNew && !t.originalContent) {
			for (const ed of vscode.window.visibleTextEditors) {
				if (ed.document.uri.fsPath === filePath) {
					await vscode.window.showTextDocument(ed.document, ed.viewColumn);
					await vscode.commands.executeCommand('workbench.action.closeActiveEditor');
				}
			}
		} else {
			const doc = await vscode.workspace.openTextDocument(t.uri);
			await vscode.window.showTextDocument(doc, { preview: false });
		}

		this.refreshStatus();
		this.notify();
	}

	async keepAll(): Promise<void> {
		for (const t of [...this.tracked.values()]) {
			if (t.status === 'pending' || t.status === 'editing') await this.keep(t.uri.fsPath);
		}
	}

	async undoAll(): Promise<void> {
		for (const t of [...this.tracked.values()]) {
			if (t.status === 'pending' || t.status === 'editing') await this.undo(t.uri.fsPath);
		}
	}

	clearSession(): void {
		for (const t of this.tracked.values()) {
			this.clearEditorDecorations(t.uri.fsPath);
		}
		this.tracked.clear();
		this.activePath = undefined;
		this.statusBar.hide();
		this._onDidChangeFileDecorations.fire(undefined);
		this.notify();
	}

	dispose(): void {
		if (this.disposed) return;
		this.disposed = true;
		this.clearSession();
		this.statusBar.dispose();
		this.addDecorationType.dispose();
		this.editingDecorationType.dispose();
		this._onDidChangeFileDecorations.dispose();
	}

	private async originalVirtualUri(t: TrackedEdit): Promise<vscode.Uri> {
		const scheme = 'aix-original';
		// Register once
		if (!AixEditSession.originalProviderRegistered) {
			AixEditSession.originalProviderRegistered = true;
			this.context.subscriptions.push(
				vscode.workspace.registerTextDocumentContentProvider(scheme, {
					provideTextDocumentContent: (uri) => {
						const key = Buffer.from(uri.path.replace(/^\//, ''), 'base64url').toString('utf8');
						return this.tracked.get(key)?.originalContent ?? '';
					},
				}),
			);
		}
		const encoded = Buffer.from(t.uri.fsPath, 'utf8').toString('base64url');
		return vscode.Uri.parse(`${scheme}:/${encoded}${path.extname(t.uri.fsPath)}`);
	}

	private static originalProviderRegistered = false;

	private applyAddDecorations(editor: vscode.TextEditor, proposedContent: string): void {
		const t = this.tracked.get(editor.document.uri.fsPath);
		const original = t?.originalContent ?? '';
		const ranges = addedLineRanges(original, proposedContent, editor.document);
		editor.setDecorations(this.editingDecorationType, []);
		editor.setDecorations(this.addDecorationType, ranges);
	}

	private reapplyDecorations(editor: vscode.TextEditor): void {
		const t = this.tracked.get(editor.document.uri.fsPath);
		if (!t || (t.status !== 'pending' && t.status !== 'editing')) return;
		if (t.status === 'editing') {
			const lineCount = Math.max(editor.document.lineCount, 1);
			editor.setDecorations(this.editingDecorationType, [new vscode.Range(0, 0, lineCount - 1, Number.MAX_SAFE_INTEGER)]);
		} else {
			this.applyAddDecorations(editor, t.proposedContent);
		}
	}

	private clearEditorDecorations(filePath: string): void {
		for (const ed of vscode.window.visibleTextEditors) {
			if (ed.document.uri.fsPath === filePath) {
				ed.setDecorations(this.addDecorationType, []);
				ed.setDecorations(this.editingDecorationType, []);
			}
		}
	}

	private refreshStatus(): void {
		const pending = [...this.tracked.values()].filter(t => t.status === 'pending' || t.status === 'editing');
		if (!pending.length) {
			this.statusBar.text = '$(check) AIX edits settled';
			setTimeout(() => { if (![...this.tracked.values()].some(t => t.status === 'pending' || t.status === 'editing')) this.statusBar.hide(); }, 2500);
			return;
		}
		const adds = pending.reduce((n, t) => n + t.addedLines, 0);
		const rems = pending.reduce((n, t) => n + t.removedLines, 0);
		this.statusBar.text = `$(diff-modified) AIX ${pending.length} file${pending.length === 1 ? '' : 's'}  +${adds}/−${rems}`;
		this.statusBar.show();
	}

	private notify(): void {
		const snap = this.summaries();
		for (const l of this.listeners) l(snap, this.activePath);
	}
}

function lineStats(original: string, proposed: string): { added: number; removed: number } {
	const a = original ? original.split(/\r?\n/) : [];
	const b = proposed ? proposed.split(/\r?\n/) : [];
	// Simple LCS-ish count via set difference on multiset — good enough for UI badges
	const aMap = new Map<string, number>();
	for (const line of a) aMap.set(line, (aMap.get(line) || 0) + 1);
	let removed = 0;
	let matched = 0;
	for (const line of b) {
		const n = aMap.get(line) || 0;
		if (n > 0) {
			aMap.set(line, n - 1);
			matched++;
		}
	}
	const added = b.length - matched;
	removed = a.length - matched;
	return { added, removed };
}

function firstChangedLine(original: string, proposed: string): number {
	const a = original.split(/\r?\n/);
	const b = proposed.split(/\r?\n/);
	const n = Math.max(a.length, b.length);
	for (let i = 0; i < n; i++) {
		if ((a[i] ?? '') !== (b[i] ?? '')) return i;
	}
	return 0;
}

function addedLineRanges(original: string, proposed: string, doc: vscode.TextDocument): vscode.Range[] {
	const a = original ? original.split(/\r?\n/) : [];
	const b = proposed ? proposed.split(/\r?\n/) : [];
	const ranges: vscode.Range[] = [];

	if (!a.length || (a.length === 1 && a[0] === '')) {
		// Entire file is new
		if (doc.lineCount > 0) {
			ranges.push(new vscode.Range(0, 0, doc.lineCount - 1, Number.MAX_SAFE_INTEGER));
		}
		return ranges;
	}

	// Mark lines in proposed that don't exist at the same index in original
	const max = Math.min(b.length, doc.lineCount);
	for (let i = 0; i < max; i++) {
		if ((a[i] ?? '') !== (b[i] ?? '')) {
			ranges.push(new vscode.Range(i, 0, i, Number.MAX_SAFE_INTEGER));
		}
	}
	if (b.length > a.length) {
		for (let i = a.length; i < Math.min(b.length, doc.lineCount); i++) {
			ranges.push(new vscode.Range(i, 0, i, Number.MAX_SAFE_INTEGER));
		}
	}
	return ranges;
}
