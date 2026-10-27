/*---------------------------------------------------------------------------------------------
 *  Shared client for AIX objective scoping (/discuss), admitting (/admit), cycle execution,
 *  and cycle progress polling (status / events / preview artifacts).
 *--------------------------------------------------------------------------------------------*/
import * as http from 'http';
import * as https from 'https';

export interface DiscussMessage {
	role: 'user' | 'assistant';
	content: string;
	/** Stable id for conversation→objective binding (AIX-REAL-EXECUTION-002). */
	message_id?: string;
	parent_message_id?: string | null;
}

export interface DiscussResponseBody {
	reply?: string;
	task_class?: string;
	proposed_scope?: unknown;
	ambiguous?: boolean;
	thread_id?: string;
	message?: string;
	detail?: unknown;
	intent?: string;
	confidence?: number;
	objective_summary?: string;
	requires_objective_confirm?: boolean;
	planner_invoked?: boolean;
}

export interface DiscussRequestOptions {
	messages: DiscussMessage[];
	thread_id?: string;
	active_objective_id?: string;
	escalate_to_planner?: boolean;
	client_message_id?: string;
	attachments?: { local_path: string; filename: string; mime: string }[];
}

export interface ProposedFile {
	name: string;
	bytes?: number;
	preview_url?: string;
}

/** AIX-REAL-EXECUTION-005 verification check, as returned by `verification_engine.py`. */
export interface VerificationCheck {
	check_type: string;
	mandatory: boolean;
	status: 'PASSED' | 'FAILED' | 'NOT_RUN' | 'BLOCKED' | string;
	evidence_ref?: string;
	failure_reason?: string | null;
	started_at?: string;
	completed_at?: string;
	verifier_version?: string;
}

/** AIX-REAL-EXECUTION-005 final execution report (`final_report.py::build_final_report`),
 * exposed on `get_cycle`'s `item.final_report`. */
export interface FinalExecutionReport {
	objective_id?: string;
	cycle_id?: string;
	trace_id?: string;
	objective?: string;
	final_status?: string;
	requested_operation?: string;
	target_resource?: string | null;
	plan_summary?: string;
	approvals?: string[];
	real_operations?: {
		real_operations: number;
		target_files_read: number;
		target_files_modified: number;
		commands_executed: number;
		tests_executed: number;
	};
	verification_results?: VerificationCheck[];
	administrative_artifacts?: string[];
	warnings?: string[];
	blocking_stage?: string | null;
	reason_code?: string | null;
	recommended_next_action?: string | null;
}

/** AIX-REAL-EXECUTION-005 canonical stage projection (`_execution_stage_projection`),
 * exposed on `get_cycle`'s `item.execution_stages`. Status only — no timestamps yet. */
export interface ExecutionStage {
	stage: string;
	status: 'pending' | 'running' | 'completed' | 'blocked' | 'failed' | 'skipped' | string;
	reason_code?: string | null;
}

export interface CycleItem {
	cycle_id?: string;
	objective_id?: string;
	phase?: string;
	/** Optimistic concurrency token — required as If-Match on cycle control POSTs. */
	version?: number;
	terminal_reason?: string | null;
	objective?: string;
	workhub_task_id?: string;
	preview_url?: string | null;
	proposed_files?: ProposedFile[];
	plan?: unknown;
	tests?: { positive?: unknown[]; negative?: unknown[] };
	/** Approval identity — echo on approve/resume (Real Execution / production closure). */
	plan_id?: string;
	plan_revision?: number;
	approval_gate_id?: string | null;
	approval_reference?: string | null;
	approved_gate_id?: string | null;
	approved_plan_revision?: number | null;
	approved_operations?: string[];
	approved_resources?: string[];
	repository_id?: string | null;
	// AIX-REAL-EXECUTION-005: backend-authored, truthful — Studio/clients must consume
	// these, never independently infer progress or success from `phase` alone.
	execution_stages?: ExecutionStage[];
	final_report?: FinalExecutionReport | null;
	real_execute?: {
		ok?: boolean;
		workspace?: string;
		artifacts?: Record<string, string>;
		token_usage?: unknown;
		detail?: string;
		evidence_refs?: string[];
		// AIX-REAL-EXECUTION-001 stage-separated outcome model. `ok` above only means
		// the orchestration call returned a usable result — it is NEVER a completion
		// signal on its own. `objective_status` is the one field that means the
		// requested work actually happened; always prefer it over `phase`.
		orchestration_status?: string;
		planning_status?: string;
		execution_status?: string;
		verification_status?: string;
		objective_status?: string;
		reason_code?: string | null;
		strategy_id?: string | null;
		remaining_asked_work?: boolean;
		real_operations?: number;
		target_files_read?: number;
		target_files_modified?: number;
		administrative_artifacts_generated?: number;
		// Real source-edit branch only — a target repository was actually inspected
		// and patched. Always separated from `run_artifacts`; never merged.
		changed_source_files?: { path: string; additions?: number; deletions?: number }[];
		run_artifacts?: string[];
		repository_root?: string | null;
		diff?: string | null;
		// Real read-file branch only — no files changed (changed_source_files stays
		// empty), so this is the signal a client checks instead. Never carries a raw
		// secret value: `env_summary` keys are name/presence/sensitivity/type only.
		read_verification?: {
			resource_resolved: boolean;
			file_read_non_empty: boolean;
			target_resource_accessed: boolean;
			secret_values_redacted: boolean;
		} | null;
		read_file?: {
			resource: string;
			bytes_read: number;
			line_count: number;
			env_summary?: {
				keys: { name: string; present: boolean; sensitive: boolean; value_type: string | null }[];
				duplicate_keys: string[];
				malformed_lines: number[];
				line_count: number;
			} | null;
		} | null;
		// Real source-edit branch only — a real, computed change against real discovered
		// content, but NOT yet applied to `repository_root`. The client applies
		// `new_content` itself (via the same propose/Keep/Undo flow as any other edit)
		// once a human reviews it; nothing here means the target file is untouched.
		proposed_edits?: { path: string; unified_diff: string; new_content: string; reason: string }[] | null;
	};
	status?: { phase?: string };
	source?: string;
}

export interface CycleDiffResponse {
	ok?: boolean;
	diff?: string | null;
	changed_source_files?: { path: string; additions?: number; deletions?: number }[];
	run_artifacts?: string[];
	repository_root?: string | null;
	[key: string]: unknown;
}

export interface CycleStatusResponse {
	ok?: boolean;
	operation?: string;
	item?: CycleItem;
	message?: string;
	detail?: unknown;
	[key: string]: unknown;
}

export interface CycleEvent {
	event?: string;
	event_type?: string;
	event_id?: string;
	sequence?: number;
	cycle_id?: string;
	at?: string;
	occurred_at?: string;
	actor_id?: string;
	workspace?: string;
	evidence_refs?: string[];
	payload_refs?: Record<string, unknown>;
	[key: string]: unknown;
}

export interface CycleEventsResponse {
	ok?: boolean;
	operation?: string;
	cycle_id?: string;
	status?: { phase?: string };
	events?: CycleEvent[];
	real_execute?: CycleItem['real_execute'];
	message?: string;
	[key: string]: unknown;
}

function extractTenantFromJwt(token: string): string | undefined {
	try {
		const parts = token.split('.');
		if (parts.length < 2) return undefined;
		const payloadJson = Buffer.from(parts[1], 'base64url').toString('utf8');
		const payload = JSON.parse(payloadJson) as Record<string, unknown>;
		const tenant = payload.tenant_id || payload.tenantId || payload.tenant;
		return typeof tenant === 'string' && tenant.trim() ? tenant.trim() : undefined;
	} catch {
		return undefined;
	}
}

function authHeaders(
	accessToken: string,
	tenantId: string | undefined,
	opts?: { contentLength?: number; idempotencyKey?: string; ifMatch?: string },
): Record<string, string | number> {
	const effectiveTenant = (accessToken ? extractTenantFromJwt(accessToken) : undefined) || tenantId;
	const headers: Record<string, string | number> = {
		'Authorization': `Bearer ${accessToken}`,
		'Cookie': `aix_v2_access_token=${accessToken}${effectiveTenant ? `; aix_v2_tenant_id=${effectiveTenant}` : ''}`,
		'Accept': 'application/json, text/*, */*',
	};
	if (opts?.contentLength !== undefined) {
		headers['Content-Type'] = 'application/json';
		headers['Content-Length'] = opts.contentLength;
	}
	if (effectiveTenant) {
		headers['X-Hyeve-Tenant-Id'] = effectiveTenant;
		headers['X-Tenant-ID'] = effectiveTenant;
	}
	if (opts?.idempotencyKey) {
		headers['Idempotency-Key'] = opts.idempotencyKey;
		headers['X-Idempotency-Key'] = opts.idempotencyKey;
	}
	// Cycle control requires If-Match = cycle.version (428 without it).
	if (opts?.ifMatch) {
		headers['If-Match'] = String(opts.ifMatch);
	}
	return headers;
}

function sendHttpRequest<T = Record<string, unknown>>(
	targetUrl: URL,
	accessToken: string,
	tenantId: string | undefined,
	options: {
		method?: 'GET' | 'POST';
		body?: unknown;
		signal?: AbortSignal;
		timeoutMs?: number;
		idempotencyKey?: string;
		ifMatch?: string;
	} = {},
): Promise<{ status: number; json: T; text: string; contentType: string }> {
	return new Promise((resolve, reject) => {
		const method = options.method || (options.body !== undefined ? 'POST' : 'GET');
		const payload = options.body !== undefined ? Buffer.from(JSON.stringify(options.body), 'utf8') : undefined;
		const transport = targetUrl.protocol === 'https:' ? https : http;
		const headers = authHeaders(accessToken, tenantId, {
			contentLength: payload?.length,
			idempotencyKey: options.idempotencyKey,
			ifMatch: options.ifMatch,
		});
		const req = transport.request(targetUrl, {
			method,
			headers,
			timeout: options.timeoutMs ?? 60_000,
			signal: options.signal,
		}, res => {
			const chunks: Buffer[] = [];
			res.on('data', chunk => chunks.push(chunk));
			res.on('end', () => {
				const text = Buffer.concat(chunks).toString('utf8');
				const contentType = String(res.headers['content-type'] || '');
				let json = {} as T;
				if (contentType.includes('json') || text.trim().startsWith('{') || text.trim().startsWith('[')) {
					try {
						json = text ? JSON.parse(text) : ({} as T);
					} catch {
						json = { message: text } as T;
					}
				} else if (text) {
					json = { message: text } as T;
				}
				resolve({ status: res.statusCode ?? 0, json, text, contentType });
			});
		});
		req.on('error', reject);
		req.on('timeout', () => req.destroy(new Error('AIX request timed out after 60s')));
		if (options.signal) {
			if (options.signal.aborted) {
				req.destroy(new Error('Cancelled by user'));
				return;
			}
			options.signal.addEventListener('abort', () => req.destroy(new Error('Cancelled by user')), { once: true });
		}
		if (payload) {
			req.write(payload);
		}
		req.end();
	});
}

function statusHint(status: number): string {
	switch (status) {
		case 401: return 'Authentication required or token expired';
		case 403: return 'Forbidden — tenant mismatch or insufficient permissions';
		case 404: return 'Endpoint not found — check aix.orchestratorUrl (BFF should be http://localhost:3000)';
		case 502: return 'Bad gateway — AIX-Frontend could not reach hyeve-aix-service';
		case 503: return 'Service unavailable — AIX backend is down';
		default: return `HTTP ${status}`;
	}
}

function baseUrl(orchestratorUrl: string): string {
	return orchestratorUrl.trim().replace(/\/$/, '');
}

/**
 * POST to the discuss endpoint with intelligent path resolution and 404 fallback.
 */
export async function postDiscuss(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	body: DiscussRequestOptions,
	signal?: AbortSignal,
): Promise<{ status: number; json: DiscussResponseBody }> {
	const raw = baseUrl(orchestratorUrl);
	if (!raw) throw new Error('Orchestrator URL is empty');

	const candidatePaths: string[] = [];
	if (raw.endsWith('/objectives/discuss')) {
		candidatePaths.push('');
	} else if (raw.endsWith('/api/aix') || raw.endsWith('/aix')) {
		candidatePaths.push('/objectives/discuss');
	} else {
		candidatePaths.push('/api/aix/objectives/discuss', '/aix/objectives/discuss', '/objectives/discuss');
	}

	// DiscussObjectiveRequest uses extra="forbid" — only role/content on messages,
	// and never client_message_id in the JSON body (that goes as Idempotency-Key).
	const discussBody: Record<string, unknown> = {
		messages: body.messages.map(m => ({ role: m.role, content: m.content })),
	};
	if (body.thread_id) discussBody.thread_id = body.thread_id;
	if (body.active_objective_id) discussBody.active_objective_id = body.active_objective_id;
	if (body.escalate_to_planner !== undefined) discussBody.escalate_to_planner = body.escalate_to_planner;
	if (body.attachments?.length) discussBody.attachments = body.attachments;

	let lastResponse: { status: number; json: DiscussResponseBody } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}

		const res = await sendHttpRequest<DiscussResponseBody>(targetUrl, accessToken, tenantId, {
			method: 'POST',
			body: discussBody,
			signal,
			idempotencyKey: body.client_message_id,
		});
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			if (lastResponse.status >= 400 && !lastResponse.json.message) {
				lastResponse.json.message = statusHint(lastResponse.status);
			}
			return lastResponse;
		}
	}

	lastResponse.json.message = statusHint(404);
	return lastResponse;
}

export interface AdmitMessage {
	message_id: string;
	role: 'user' | 'assistant';
	content: string;
	parent_message_id?: string | null;
}

export interface AdmitRequestBody {
	objective: string;
	task_class: string;
	repository_id: string;
	allowed_files?: unknown;
	/** Conversation → objective binding (Sprint 002). When present, backend binds latest intended message — never an earlier "Hey". */
	conversation_id?: string;
	messages?: AdmitMessage[];
	selected_message_id?: string;
	objective_source_message_id?: string;
}

/**
 * POST to admit an objective into AIX execution.
 */
export async function postAdmit(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	body: AdmitRequestBody,
	signal?: AbortSignal,
	idempotencyKey?: string,
): Promise<{ status: number; json: Record<string, unknown> }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		'/api/aix/objectives/admit',
		'/aix/objectives/admit',
		'/objectives/admit',
	];

	const admitBody: Record<string, unknown> = {
		workhub_task_id: 'auto',
		create_workhub_task: true,
		objective: body.objective,
		task_class: body.task_class || 'bounded_repair',
		definition_of_done: ['Implementation completed', 'Tests passing'],
		// Required by the backend — resolves which approved repository (see
		// OBJECTIVE_CYCLE_REPOSITORY_ROOTS) the real source-edit branch may touch.
		// Admitting without this has always 422'd before reaching any execution code.
		repository_id: body.repository_id,
		allowed_files: body.allowed_files || [],
	};
	if (body.conversation_id) admitBody.conversation_id = body.conversation_id;
	if (body.messages?.length) admitBody.messages = body.messages;
	if (body.selected_message_id) admitBody.selected_message_id = body.selected_message_id;
	if (body.objective_source_message_id) {
		admitBody.objective_source_message_id = body.objective_source_message_id;
	}

	let lastResponse: { status: number; json: Record<string, unknown> } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<Record<string, unknown>>(targetUrl, accessToken, tenantId, {
			method: 'POST',
			body: admitBody,
			signal,
			idempotencyKey: idempotencyKey || `admit-${Date.now()}`,
		});
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			if (lastResponse.status >= 400 && !lastResponse.json.message) {
				lastResponse.json.message = statusHint(lastResponse.status);
			}
			return lastResponse;
		}
	}

	lastResponse.json.message = statusHint(404);
	return lastResponse;
}

/**
 * POST to start an execution cycle for an admitted objective.
 */
export async function postStartCycle(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	objectiveId: string,
	signal?: AbortSignal,
	idempotencyKey?: string,
): Promise<{ status: number; json: Record<string, unknown> }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/objectives/${encodeURIComponent(objectiveId)}/cycles`,
		`/aix/objectives/${encodeURIComponent(objectiveId)}/cycles`,
		`/objectives/${encodeURIComponent(objectiveId)}/cycles`,
	];

	const cycleBody = {
		// StartCycleRequest requires candidate_sha (min 7). Studio local runs use a
		// placeholder — real_execute does not gate on this SHA for workspace edits.
		candidate_sha: 'studio0',
		budgets: { max_steps: 5 },
	};
	let lastResponse: { status: number; json: Record<string, unknown> } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<Record<string, unknown>>(targetUrl, accessToken, tenantId, {
			method: 'POST',
			body: cycleBody,
			signal,
			idempotencyKey: idempotencyKey || `cycle-${objectiveId}`,
		});
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			if (lastResponse.status >= 400 && !lastResponse.json.message) {
				lastResponse.json.message = statusHint(lastResponse.status);
			}
			return lastResponse;
		}
	}

	lastResponse.json.message = statusHint(404);
	return lastResponse;
}

/**
 * POST cycle control: cancel | resume | block | reject | clarify | retry-step | replan | approval-reference
 */
export async function postCycleAction(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	action: string,
	signal?: AbortSignal,
	idempotencyKey?: string,
	body?: Record<string, unknown>,
	/** Cycle version from GET /cycles/{id} — required by backend (If-Match). */
	ifMatch?: string | number,
): Promise<{ status: number; json: Record<string, unknown> }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/${encodeURIComponent(action)}`,
		`/aix/cycles/${encodeURIComponent(cycleId)}/${encodeURIComponent(action)}`,
		`/cycles/${encodeURIComponent(cycleId)}/${encodeURIComponent(action)}`,
	];
	// CycleControlRequest — only forward allowlisted fields.
	// Never send cycle_id (it is already in the URL path).
	const allowedKeys = new Set([
		'reason',
		'clarification',
		'step_id',
		'approval_reference',
		'approval_request_id',
		'replan_input',
		'metadata',
		// Approval identity (Real Execution) — prove which plan/gate is being approved.
		'approval_gate_id',
		'plan_revision',
		'plan_id',
	]);
	const payload: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(body || {})) {
		if (allowedKeys.has(key) && value !== undefined) payload[key] = value;
	}
	let lastResponse: { status: number; json: Record<string, unknown> } = { status: 404, json: { message: 'Not Found' } };
	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<Record<string, unknown>>(targetUrl, accessToken, tenantId, {
			method: 'POST',
			body: payload,
			signal,
			idempotencyKey: idempotencyKey || `ctrl-${action}-${cycleId}-${Date.now()}`,
			ifMatch: ifMatch !== undefined && ifMatch !== null ? String(ifMatch) : undefined,
		});
		lastResponse = { status: res.status, json: res.json };
		// BFF returns 400 for unknown actions; keep falling through to /aix/... on gateway.
		if (lastResponse.status !== 404 && lastResponse.status !== 400) return lastResponse;
		if (lastResponse.status === 400) {
			const err = String((lastResponse.json as { error?: string }).error || '');
			if (err !== 'INVALID_CYCLE_OR_ACTION') return lastResponse;
		}
	}
	return lastResponse;
}

/**
 * GET cycle plan (`GET /api/aix/cycles/{id}/plan`).
 */
export async function getCyclePlan(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	signal?: AbortSignal,
): Promise<{ status: number; json: Record<string, unknown> }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/plan`,
		`/aix/cycles/${encodeURIComponent(cycleId)}/plan`,
		`/cycles/${encodeURIComponent(cycleId)}/plan`,
	];
	let lastResponse: { status: number; json: Record<string, unknown> } = { status: 404, json: { message: 'Not Found' } };
	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<Record<string, unknown>>(targetUrl, accessToken, tenantId, { method: 'GET', signal, timeoutMs: 30_000 });
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) return lastResponse;
	}
	return lastResponse;
}

/**
 * GET cycle status — primary progress poll (`GET /api/aix/cycles/{id}`).
 */
export async function getCycle(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	signal?: AbortSignal,
): Promise<{ status: number; json: CycleStatusResponse }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}`,
		`/aix/cycles/${encodeURIComponent(cycleId)}`,
		`/cycles/${encodeURIComponent(cycleId)}`,
	];

	let lastResponse: { status: number; json: CycleStatusResponse } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<CycleStatusResponse>(targetUrl, accessToken, tenantId, { method: 'GET', signal, timeoutMs: 30_000 });
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			if (lastResponse.status >= 400 && !lastResponse.json.message) {
				lastResponse.json.message = statusHint(lastResponse.status);
			}
			return lastResponse;
		}
	}

	lastResponse.json.message = statusHint(404);
	return lastResponse;
}

/**
 * GET cycle event panel (`GET /api/aix/cycles/{id}/panels/events` or `/events`).
 */
export async function getCycleEvents(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	signal?: AbortSignal,
): Promise<{ status: number; json: CycleEventsResponse }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/panels/events`,
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/events`,
		`/aix/cycles/${encodeURIComponent(cycleId)}/events`,
		`/cycles/${encodeURIComponent(cycleId)}/events`,
	];

	let lastResponse: { status: number; json: CycleEventsResponse } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<CycleEventsResponse>(targetUrl, accessToken, tenantId, { method: 'GET', signal, timeoutMs: 30_000 });
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			return lastResponse;
		}
	}

	return lastResponse;
}

/**
 * GET proposed artifact text (`GET /api/aix/cycles/{id}/preview/{artifact}`).
 */
export async function getCyclePreview(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	artifact: string,
	signal?: AbortSignal,
): Promise<{ status: number; text: string; contentType: string }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/preview/${encodeURIComponent(artifact)}`,
		`/aix/cycles/${encodeURIComponent(cycleId)}/preview/${encodeURIComponent(artifact)}`,
		`/cycles/${encodeURIComponent(cycleId)}/preview/${encodeURIComponent(artifact)}`,
	];

	let last: { status: number; text: string; contentType: string } = { status: 404, text: '', contentType: '' };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest(targetUrl, accessToken, tenantId, { method: 'GET', signal, timeoutMs: 30_000 });
		last = { status: res.status, text: res.text, contentType: res.contentType };
		if (res.status !== 404) {
			return last;
		}
	}

	return last;
}

/**
 * GET the real target-repository diff for a cycle (`GET /api/aix/cycles/{id}/diff`),
 * split into `changed_source_files` (real repo edits) vs. `run_artifacts` (internal
 * AIX bookkeeping) — never merged. Used to render an actual diff of an existing
 * repository file instead of materializing AIX output into a side folder.
 */
export async function getCycleDiff(
	orchestratorUrl: string,
	accessToken: string,
	tenantId: string | undefined,
	cycleId: string,
	signal?: AbortSignal,
): Promise<{ status: number; json: CycleDiffResponse }> {
	const raw = baseUrl(orchestratorUrl);
	const candidatePaths = [
		`/api/aix/cycles/${encodeURIComponent(cycleId)}/diff`,
		`/aix/cycles/${encodeURIComponent(cycleId)}/diff`,
		`/cycles/${encodeURIComponent(cycleId)}/diff`,
	];

	let lastResponse: { status: number; json: CycleDiffResponse } = { status: 404, json: { message: 'Not Found' } };

	for (const candidatePath of candidatePaths) {
		let targetUrl: URL;
		try {
			targetUrl = new URL(raw + candidatePath);
		} catch {
			continue;
		}
		const res = await sendHttpRequest<CycleDiffResponse>(targetUrl, accessToken, tenantId, { method: 'GET', signal, timeoutMs: 30_000 });
		lastResponse = { status: res.status, json: res.json };
		if (lastResponse.status !== 404) {
			return lastResponse;
		}
	}

	return lastResponse;
}

/** Truly finished — do not keep watching. */
export const TERMINAL_CYCLE_PHASES = new Set([
	'COMPLETED',
	'FAILED',
	'CANCELLED',
	'CANCELED',
	'REJECTED',
]);

/** Phases that mean the Active Run panel should not stay open as "live". */
export const FINISHED_ACTIVE_RUN_PHASES = new Set([
	...TERMINAL_CYCLE_PHASES,
	'COMPLETED_FOR_REVIEW',
]);

/** True when a locally persisted active-run phase is already finished. */
export function isStoredActiveRunFinished(phase: string | undefined): boolean {
	return FINISHED_ACTIVE_RUN_PHASES.has(String(phase || '').toUpperCase());
}

/** Needs operator action; keep active task, do not treat as dead. */
export const AWAITING_USER_PHASES = new Set([
	'WAITING_APPROVAL',
	'AWAITING_APPROVAL',
	'BLOCKED',
	'PAUSED',
	'WAITING_BLOCKER',
]);

/**
 * AIX-REAL-EXECUTION-001/005 truthful outcome. `phase` is the *orchestration*
 * state (the workflow engine's own control-plane status — e.g. the real terminal
 * phase on a successful run is `COMPLETED_FOR_REVIEW`, never the literal string
 * `COMPLETED`, so a `phase === 'COMPLETED'` check alone never fires on the happy
 * path). `real_execute.objective_status` is the backend's own derived truth about
 * whether the requested work actually happened — always prefer it. Falls back to
 * `phase` only when `real_execute` does not exist yet (still admitting/planning),
 * and even then never claims `completed` from `phase` alone unless `phase` is
 * *literally* `COMPLETED` (kept only for backward compatibility with any caller
 * that already relies on that exact string).
 */
export type ObjectiveOutcome =
	| 'completed'
	| 'completed_with_warnings'
	| 'blocked'
	| 'failed'
	| 'awaiting_review'
	| 'incomplete'
	| 'unknown';

export function objectiveOutcome(item: CycleItem | undefined): ObjectiveOutcome {
	const phase = String(item?.phase || item?.status?.phase || '').toUpperCase();
	const reason = String(item?.terminal_reason || item?.real_execute?.reason_code || '').toUpperCase();
	const status = String(item?.real_execute?.objective_status || '').toUpperCase();
	const blockedLike =
		phase === 'BLOCKED'
		|| phase === 'FAILED'
		|| reason.includes('REPAIR')
		|| reason.includes('HALT')
		|| reason.includes('UNSUPPORTED_CAPABILITY')
		|| reason.includes('STRATEGY_INSUFFICIENT');
	// AIX-EC-004 / EC-08: BLOCKED or REPAIR/HALT must never display as COMPLETED.
	if (blockedLike && (status === 'COMPLETED' || status === 'COMPLETED_WITH_WARNINGS')) {
		return phase === 'FAILED' ? 'failed' : 'blocked';
	}
	switch (status) {
		case 'COMPLETED': return 'completed';
		case 'COMPLETED_WITH_WARNINGS': return 'completed_with_warnings';
		case 'BLOCKED': return 'blocked';
		case 'FAILED': return 'failed';
		case 'AWAITING_REVIEW': return 'awaiting_review';
		case 'INCOMPLETE': return 'incomplete';
	}
	// Orchestration happy-path terminal — Studio must stop the "running" spinner even
	// when real_execute.objective_status is briefly missing from the poll payload.
	if (phase === 'COMPLETED_FOR_REVIEW') return 'awaiting_review';
	if (TERMINAL_CYCLE_PHASES.has(phase)) {
		return phase === 'COMPLETED' ? 'completed' : 'failed';
	}
	if (phase === 'BLOCKED') return 'blocked';
	return 'unknown';
}

/** True once the objective has reached a real terminal outcome — by truthful
 * `objective_status` when available, otherwise by orchestration `phase`. Use this
 * (not a bare `TERMINAL_CYCLE_PHASES.has(phase)` check) to decide whether to stop
 * polling: a cycle can sit in a non-`TERMINAL_CYCLE_PHASES` orchestration phase
 * (e.g. `COMPLETED_FOR_REVIEW`) while `objective_status` already proves the
 * objective itself is done. */
export function isObjectiveTerminal(item: CycleItem | undefined): boolean {
	const phase = String(item?.phase || item?.status?.phase || '').toUpperCase();
	const outcome = objectiveOutcome(item);
	if (phase === 'COMPLETED_FOR_REVIEW') return true;
	return TERMINAL_CYCLE_PHASES.has(phase) || (outcome !== 'unknown' && outcome !== 'incomplete');
}

/**
 * When a cycle is done/awaiting review, force any leftover `running` plan chips to a
 * settled status so the Active Run panel does not look permanently stuck on
 * "Execute cycle" after the backend already reached COMPLETED_FOR_REVIEW.
 */
export function settlePlanForOutcome(
	plan: { id: string; label: string; status: 'done' | 'running' | 'todo' | 'error' }[],
	outcome: ObjectiveOutcome,
): { id: string; label: string; status: 'done' | 'running' | 'todo' | 'error' }[] {
	if (!plan.length) return plan;
	const failed = outcome === 'blocked' || outcome === 'failed';
	const doneLike = outcome === 'completed' || outcome === 'completed_with_warnings' || outcome === 'awaiting_review';
	if (!failed && !doneLike) return plan;
	return plan.map(s => {
		if (s.status === 'done' || s.status === 'error') return s;
		if (failed && (s.status === 'running' || s.status === 'todo')) {
			return { ...s, status: s.status === 'running' ? 'error' : s.status };
		}
		// completed / awaiting_review: running + remaining todos that were never
		// started after a successful read (e.g. Propose files) stay todo — only
		// clear the stuck ● running chip.
		if (doneLike && s.status === 'running') return { ...s, status: 'done' };
		return s;
	});
}

/** Short, human-readable label for `objectiveOutcome()` — used anywhere the UI
 * previously assumed `phase === 'COMPLETED'` meant success. */
export function objectiveOutcomeLabel(outcome: ObjectiveOutcome): string {
	switch (outcome) {
		case 'completed': return 'Completed';
		case 'completed_with_warnings': return 'Completed (with warnings)';
		case 'blocked': return 'Blocked';
		case 'failed': return 'Failed';
		case 'awaiting_review': return 'Awaiting review';
		case 'incomplete': return 'In progress';
		default: return 'Unknown';
	}
}

/** Map backend `execution_stages` into the active-run plan chips. Never invent stages. */
export function planStepsFromExecutionStages(stages: ExecutionStage[] | undefined): {
	id: string;
	label: string;
	status: 'done' | 'running' | 'todo' | 'error';
}[] {
	if (!stages?.length) return [];
	return stages.map((s, i) => {
		const st = String(s.status || 'pending').toLowerCase();
		let status: 'done' | 'running' | 'todo' | 'error' = 'todo';
		if (st === 'completed' || st === 'skipped') status = 'done';
		else if (st === 'running') status = 'running';
		else if (st === 'blocked' || st === 'failed') status = 'error';
		const reason = s.reason_code ? ` (${s.reason_code})` : '';
		return { id: `stage-${i}-${s.stage}`, label: `${s.stage}${reason}`, status };
	});
}

/** Extract admit error code from BFF/backend error shapes. */
export function admitErrorCode(json: Record<string, unknown>): string | undefined {
	const err = json.error;
	if (err && typeof err === 'object' && err !== null && 'code' in err) {
		return String((err as { code: unknown }).code || '') || undefined;
	}
	if (typeof json.code === 'string') return json.code;
	return undefined;
}
