/*---------------------------------------------------------------------------------------------
 *  AIX Discuss — chat participant that proxies the objective-scoping "discuss" flow through
 *  the AIX-Frontend BFF (/api/aix/objectives/discuss). The BFF signs the HMAC gateway
 *  assertion server-side, so this extension never needs to hold that secret — only a real
 *  access token obtained via AixAuthManager's own sign-in (see auth.ts), never a
 *  hand-copied browser cookie.
 *--------------------------------------------------------------------------------------------*/
import * as vscode from 'vscode';
import { AixAuthManager } from './auth';
import { AixChatViewProvider } from './chatView';
import { DiscussMessage, DiscussResponseBody, postDiscuss } from './discussApi';
import { AixLanguageModelChatProvider } from './modelProvider';

function extractResponseText(turn: vscode.ChatResponseTurn): string {
	const parts: string[] = [];
	for (const part of turn.response) {
		if (part instanceof vscode.ChatResponseMarkdownPart) {
			const value = part.value;
			parts.push(typeof value === 'string' ? value : value.value);
		}
	}
	return parts.join('');
}

function buildMessages(chatContext: vscode.ChatContext, currentPrompt: string): DiscussMessage[] {
	const messages: DiscussMessage[] = [];
	for (const turn of chatContext.history) {
		if (turn instanceof vscode.ChatRequestTurn) {
			if (turn.prompt.trim()) {
				messages.push({ role: 'user', content: turn.prompt });
			}
		} else if (turn instanceof vscode.ChatResponseTurn) {
			const text = extractResponseText(turn);
			if (text.trim()) {
				messages.push({ role: 'assistant', content: text });
			}
		}
	}
	messages.push({ role: 'user', content: currentPrompt });
	return messages;
}

/** The BFF returns the thread_id in the JSON body; we stash it on the ChatResult's
 * metadata so the next turn in this same chat session can find and resume it. */
function findLastThreadId(chatContext: vscode.ChatContext): string | undefined {
	for (let i = chatContext.history.length - 1; i >= 0; i--) {
		const turn = chatContext.history[i];
		if (turn instanceof vscode.ChatResponseTurn) {
			const metadata = turn.result.metadata as { threadId?: string } | undefined;
			if (metadata?.threadId) {
				return metadata.threadId;
			}
		}
	}
	return undefined;
}

export function activate(context: vscode.ExtensionContext): void {
	const auth = new AixAuthManager(context);
	context.subscriptions.push(
		vscode.commands.registerCommand('aix.signIn', () => auth.signIn()),
		vscode.commands.registerCommand('aix.signOut', () => auth.signOut()),
	);

	context.subscriptions.push(
		vscode.window.registerWebviewViewProvider(
			AixChatViewProvider.viewType,
			new AixChatViewProvider(context.extensionUri, auth, context),
			{ webviewOptions: { retainContextWhenHidden: true } },
		),
	);

	const handler: vscode.ChatRequestHandler = async (request, chatContext, stream, token) => {
		const config = vscode.workspace.getConfiguration('aix');
		const orchestratorUrl = config.get<string>('orchestratorUrl', 'http://localhost:3000') || 'http://localhost:3000';
		const accessToken = await auth.getAccessToken();
		const tenantId = await auth.getTenantId();

		if (!accessToken) {
			stream.markdown('Not signed in to AIX. Run the **AIX: Sign In** command (or click the AIX status bar item), then ask again.');
			return { metadata: { error: 'missing_token' } };
		}

		const messages = buildMessages(chatContext, request.prompt);
		const threadId = findLastThreadId(chatContext);

		stream.progress('Asking AIX...');

		let response: { status: number; json: DiscussResponseBody };
		try {
			response = await postDiscuss(orchestratorUrl, accessToken, tenantId, {
				messages,
				...(threadId ? { thread_id: threadId } : {}),
			});
		} catch (err) {
			stream.markdown(`Could not reach AIX orchestrator at \`${orchestratorUrl}\`: ${err instanceof Error ? err.message : String(err)}`);
			return { metadata: { error: 'network_error' } };
		}

		if (token.isCancellationRequested) {
			return { metadata: {} };
		}

		if (response.status === 401) {
			stream.markdown('AIX session is no longer valid. Run **AIX: Sign In** again.');
			return { metadata: { error: 'unauthorized' } };
		}
		if (response.status === 503) {
			stream.markdown(`AIX objective-cycle backend is unavailable: ${response.json.message ?? 'unknown error'}`);
			return { metadata: { error: 'backend_unavailable' } };
		}
		if (response.status >= 400) {
			stream.markdown(`AIX discuss request failed (${response.status}): ${response.json.message ?? JSON.stringify(response.json)}`);
			return { metadata: { error: 'request_failed', status: response.status } };
		}

		const reply = response.json.reply ?? '(no reply)';
		stream.markdown(reply);

		if (response.json.task_class || response.json.proposed_scope) {
			const details: string[] = [];
			if (response.json.task_class) {
				details.push(`**Task class:** ${response.json.task_class}`);
			}
			if (response.json.proposed_scope) {
				details.push(`**Proposed scope:** ${JSON.stringify(response.json.proposed_scope)}`);
			}
			if (response.json.ambiguous) {
				details.push('_AIX flagged this as ambiguous — expect a clarifying question._');
			}
			stream.markdown('\n\n---\n' + details.join('\n\n'));
		}

		return { metadata: { threadId: response.json.thread_id } };
	};

	const participant = vscode.chat.createChatParticipant('aix.discuss', handler);
	participant.iconPath = vscode.Uri.joinPath(context.extensionUri, 'media', 'aix-icon.svg');
	context.subscriptions.push(participant);

	context.subscriptions.push(
		vscode.lm.registerLanguageModelChatProvider('aix', new AixLanguageModelChatProvider(auth))
	);
}

export function deactivate(): void { }
