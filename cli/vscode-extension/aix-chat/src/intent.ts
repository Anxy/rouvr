/*---------------------------------------------------------------------------------------------
 *  Client-side agent intent classifier.
 *
 *  Maps product intents (CREATE_OBJECTIVE … GENERAL_CHAT) onto the real AIX discuss/cycle
 *  surface. Only CREATE_OBJECTIVE may admit a new objective. Follow-ups control or query
 *  the active cycle instead of spawning duplicates.
 *--------------------------------------------------------------------------------------------*/

export type AgentIntent =
	| 'CREATE_OBJECTIVE'
	| 'UPDATE_OBJECTIVE'
	| 'STATUS_QUERY'
	| 'PAUSE'
	| 'RESUME'
	| 'CANCEL'
	| 'APPROVE'
	| 'REJECT'
	| 'REQUEST_DIFF'
	| 'REQUEST_TESTS'
	| 'GENERAL_CHAT';

export interface IntentResult {
	intent: AgentIntent;
	confidence: number;
	/** Maps onto backend ChatIntent when posting discuss. */
	discussIntentHint?: 'conversation' | 'objective' | 'clarification' | 'continue_objective';
	reason: string;
}

const RE = {
	status: /\b(done\??|status|progress|how('?s| is) it going|where are we|what('?s| is) (happening|left)|update me|finished\??|complete\??)\b/i,
	pause: /\b(pause|hold|wait|stop for now|block)\b/i,
	resume: /\b(resume|unpause|continue|keep going|carry on)\b/i,
	cancel: /\b(cancel|abort|kill|stop( it| this| the (run|cycle|task))?)\b/i,
	approve: /\b(approve|lgtm|ship it|looks good|accept)\b/i,
	reject: /\b(reject|deny|nope|don'?t (ship|merge)|send back)\b/i,
	diff: /\b(diff|show changes|what changed|code changes|review (the )?diff|open (the )?diff)\b/i,
	tests: /\b(tests?|test results?|verification|did tests pass|run tests)\b/i,
	update: /\b(also|instead|change (it|that)|update (the )?(objective|task|scope)|make it|actually)\b/i,
	create: /\b(build|create|implement|fix|add|refactor|migrate|generate|write|make|design)\b/i,
	trivial: /^\s*(hi|hello|hey|yo|thanks|thank you|ok|okay|cool|great)[\s!?.]*$/i,
};

/** Align with backend `message_binding.is_pure_greeting` — never admit these as objective text. */
export function isPureGreeting(text: string): boolean {
	return RE.trivial.test((text || '').trim());
}

export function classifyAgentIntent(
	text: string,
	opts: { hasActiveObjective: boolean; hasActiveCycle: boolean; mode?: string },
): IntentResult {
	const t = (text || '').trim();
	if (!t) {
		return { intent: 'GENERAL_CHAT', confidence: 1, discussIntentHint: 'clarification', reason: 'empty' };
	}

	const active = opts.hasActiveObjective || opts.hasActiveCycle;

	if (RE.status.test(t)) {
		return { intent: 'STATUS_QUERY', confidence: 0.95, discussIntentHint: 'continue_objective', reason: 'status_keywords' };
	}
	if (RE.diff.test(t)) {
		return { intent: 'REQUEST_DIFF', confidence: 0.95, discussIntentHint: 'continue_objective', reason: 'diff_keywords' };
	}
	if (RE.tests.test(t)) {
		return { intent: 'REQUEST_TESTS', confidence: 0.9, discussIntentHint: 'continue_objective', reason: 'tests_keywords' };
	}
	if (RE.cancel.test(t) && active) {
		return { intent: 'CANCEL', confidence: 0.92, reason: 'cancel_keywords' };
	}
	if (RE.pause.test(t) && active) {
		return { intent: 'PAUSE', confidence: 0.9, reason: 'pause_keywords' };
	}
	if (RE.resume.test(t) && active) {
		return { intent: 'RESUME', confidence: 0.9, reason: 'resume_keywords' };
	}
	if (RE.approve.test(t) && active) {
		return { intent: 'APPROVE', confidence: 0.85, reason: 'approve_keywords' };
	}
	if (RE.reject.test(t) && active) {
		return { intent: 'REJECT', confidence: 0.85, reason: 'reject_keywords' };
	}

	if (RE.trivial.test(t) || t.length <= 3) {
		return { intent: 'GENERAL_CHAT', confidence: 0.95, discussIntentHint: 'conversation', reason: 'greeting' };
	}

	if (active && RE.update.test(t)) {
		return { intent: 'UPDATE_OBJECTIVE', confidence: 0.8, discussIntentHint: 'continue_objective', reason: 'update_with_active' };
	}

	// Short follow-ups while a run is active → status, not a new objective
	if (active && t.split(/\s+/).length <= 6 && !RE.create.test(t)) {
		return { intent: 'STATUS_QUERY', confidence: 0.75, discussIntentHint: 'continue_objective', reason: 'short_followup_with_active' };
	}

	if (RE.create.test(t) || t.length >= 40 || opts.mode === 'agent' || opts.mode === 'debug') {
		if (active && RE.resume.test(t)) {
			return { intent: 'RESUME', confidence: 0.8, reason: 'continue_active' };
		}
		// Creating a second objective only when user clearly starts new work OR no active task
		if (active && !/\b(new|another|different|fresh)\b/i.test(t) && t.split(/\s+/).length <= 12) {
			return { intent: 'UPDATE_OBJECTIVE', confidence: 0.7, discussIntentHint: 'continue_objective', reason: 'active_prefers_update' };
		}
		return { intent: 'CREATE_OBJECTIVE', confidence: 0.85, discussIntentHint: 'objective', reason: 'create_keywords' };
	}

	return { intent: 'GENERAL_CHAT', confidence: 0.6, discussIntentHint: 'conversation', reason: 'default_chat' };
}
