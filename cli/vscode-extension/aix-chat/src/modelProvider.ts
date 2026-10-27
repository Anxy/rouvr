/*---------------------------------------------------------------------------------------------
 *  AIX language model provider — registers "aix" as a chat model vendor so it is selectable
 *  in the built-in Agent Mode model picker. VS Code's own Agent Mode drives the tool-calling
 *  loop (file edits, terminal, etc. are its own built-in tools); this provider only has to
 *  translate that loop's messages/tools into a request against hyeve-integration-service's
 *  OpenAI-compatible endpoint (POST /api/integrations/llm/openai/v1/chat/completions) and
 *  translate the reply back.
 *--------------------------------------------------------------------------------------------*/
import * as http from 'http';
import * as https from 'https';
import * as vscode from 'vscode';
import { AixAuthManager } from './auth';

const MODEL_ID = 'aix-coder';

interface OpenAiToolCall {
	id: string;
	type: 'function';
	function: { name: string; arguments: string };
}

interface OpenAiMessage {
	role: 'system' | 'user' | 'assistant' | 'tool';
	content: string | null;
	tool_calls?: OpenAiToolCall[];
	tool_call_id?: string;
}

interface OpenAiChoice {
	message?: {
		content?: string | null;
		tool_calls?: OpenAiToolCall[];
	};
	finish_reason?: string;
}

interface OpenAiChatCompletionResponse {
	choices?: OpenAiChoice[];
	error?: { message?: string };
	message?: string;
}

function textFromToolResultContent(content: unknown[]): string {
	const parts: string[] = [];
	for (const item of content) {
		if (item instanceof vscode.LanguageModelTextPart) {
			parts.push(item.value);
		} else if (typeof item === 'string') {
			parts.push(item);
		}
	}
	return parts.join('');
}

function toOpenAiMessages(messages: readonly vscode.LanguageModelChatRequestMessage[]): OpenAiMessage[] {
	const result: OpenAiMessage[] = [];

	for (const message of messages) {
		const isUser = message.role === vscode.LanguageModelChatMessageRole.User;
		const textParts: string[] = [];
		const toolCalls: OpenAiToolCall[] = [];
		const toolResultMessages: OpenAiMessage[] = [];

		for (const part of message.content) {
			if (part instanceof vscode.LanguageModelTextPart) {
				textParts.push(part.value);
			} else if (part instanceof vscode.LanguageModelToolCallPart) {
				toolCalls.push({
					id: part.callId,
					type: 'function',
					function: { name: part.name, arguments: JSON.stringify(part.input ?? {}) },
				});
			} else if (part instanceof vscode.LanguageModelToolResultPart) {
				toolResultMessages.push({
					role: 'tool',
					tool_call_id: part.callId,
					content: textFromToolResultContent(part.content as unknown[]),
				});
			}
		}

		if (textParts.length || toolCalls.length) {
			result.push({
				role: isUser ? 'user' : 'assistant',
				content: textParts.length ? textParts.join('') : null,
				...(toolCalls.length ? { tool_calls: toolCalls } : {}),
			});
		}
		result.push(...toolResultMessages);
	}

	return result;
}

function toOpenAiTools(tools: readonly vscode.LanguageModelChatTool[] | undefined): unknown[] | undefined {
	if (!tools || tools.length === 0) {
		return undefined;
	}
	return tools.map(tool => ({
		type: 'function',
		function: {
			name: tool.name,
			description: tool.description,
			parameters: tool.inputSchema ?? { type: 'object', properties: {} },
		},
	}));
}

function postChatCompletion(baseUrl: string, headers: Record<string, string>, body: unknown): Promise<{ status: number; json: OpenAiChatCompletionResponse }> {
	return new Promise((resolve, reject) => {
		let url: URL;
		try {
			url = new URL('/api/integrations/llm/openai/v1/chat/completions', baseUrl);
		} catch (err) {
			reject(err);
			return;
		}
		const payload = Buffer.from(JSON.stringify(body), 'utf8');
		const transport = url.protocol === 'https:' ? https : http;
		const req = transport.request(url, {
			method: 'POST',
			headers: {
				...headers,
				'Content-Type': 'application/json',
				'Content-Length': payload.length,
			},
			timeout: 120_000,
		}, res => {
			const chunks: Buffer[] = [];
			res.on('data', chunk => chunks.push(chunk));
			res.on('end', () => {
				const text = Buffer.concat(chunks).toString('utf8');
				let json: OpenAiChatCompletionResponse = {};
				try {
					json = text ? JSON.parse(text) : {};
				} catch {
					json = { message: text };
				}
				resolve({ status: res.statusCode ?? 0, json });
			});
		});
		req.on('error', reject);
		req.on('timeout', () => req.destroy(new Error('AIX model request timed out')));
		req.write(payload);
		req.end();
	});
}

export class AixLanguageModelChatProvider implements vscode.LanguageModelChatProvider {

	constructor(private readonly auth: AixAuthManager) { }

	async provideLanguageModelChatInformation(_options: vscode.PrepareLanguageModelChatModelOptions, _token: vscode.CancellationToken): Promise<vscode.LanguageModelChatInformation[]> {
		const config = vscode.workspace.getConfiguration('aix');
		const integrationUrl = config.get<string>('integrationUrl', '');
		const signedIn = Boolean(await this.auth.getUsername());
		if (!integrationUrl || !signedIn) {
			// Nothing usable without being signed in + an integration URL; report no models
			// rather than a model that will fail on every request.
			return [];
		}
		return [{
			id: MODEL_ID,
			name: 'AIX',
			family: 'aix',
			version: '1.0',
			tooltip: 'AIX, routed through hyeve-integration-service',
			maxInputTokens: 128_000,
			maxOutputTokens: 8_192,
			capabilities: { toolCalling: true },
		}];
	}

	async provideLanguageModelChatResponse(
		_model: vscode.LanguageModelChatInformation,
		messages: readonly vscode.LanguageModelChatRequestMessage[],
		options: vscode.ProvideLanguageModelChatResponseOptions,
		progress: vscode.Progress<vscode.LanguageModelResponsePart>,
		token: vscode.CancellationToken,
	): Promise<void> {
		const config = vscode.workspace.getConfiguration('aix');
		const integrationUrl = config.get<string>('integrationUrl', '');
		const modelName = config.get<string>('model', '');
		const accessToken = await this.auth.getAccessToken();
		const tenantId = await this.auth.getTenantId();

		if (!integrationUrl || !accessToken) {
			progress.report(new vscode.LanguageModelTextPart(
				!integrationUrl
					? 'AIX model is not configured. Set `aix.integrationUrl` (hyeve-integration-service base URL) in Settings.'
					: 'Not signed in to AIX. Run the "AIX: Sign In" command, then try again.'
			));
			return;
		}

		const headers: Record<string, string> = {
			Authorization: `Bearer ${accessToken}`,
		};
		if (tenantId) {
			headers['X-Hyeve-Tenant-Id'] = tenantId;
		}

		const body: Record<string, unknown> = {
			model: modelName || undefined,
			messages: toOpenAiMessages(messages),
			stream: false,
		};
		const tools = toOpenAiTools(options.tools);
		if (tools) {
			body.tools = tools;
			body.tool_choice = options.toolMode === vscode.LanguageModelChatToolMode.Required ? 'required' : 'auto';
		}

		let response: { status: number; json: OpenAiChatCompletionResponse };
		try {
			response = await postChatCompletion(integrationUrl, headers, body);
		} catch (err) {
			progress.report(new vscode.LanguageModelTextPart(
				`Could not reach hyeve-integration-service at \`${integrationUrl}\`: ${err instanceof Error ? err.message : String(err)}`
			));
			return;
		}

		if (token.isCancellationRequested) {
			return;
		}

		if (response.status >= 400) {
			const message = response.json.error?.message ?? response.json.message ?? `HTTP ${response.status}`;
			progress.report(new vscode.LanguageModelTextPart(`AIX model request failed: ${message}`));
			return;
		}

		const choice = response.json.choices?.[0];
		const content = choice?.message?.content;
		if (content) {
			progress.report(new vscode.LanguageModelTextPart(content));
		}
		for (const toolCall of choice?.message?.tool_calls ?? []) {
			let input: object = {};
			try {
				input = toolCall.function.arguments ? JSON.parse(toolCall.function.arguments) : {};
			} catch {
				// leave input empty rather than throw; the model will see an empty-arg tool call
			}
			progress.report(new vscode.LanguageModelToolCallPart(toolCall.id, toolCall.function.name, input));
		}
	}

	provideTokenCount(_model: vscode.LanguageModelChatInformation, text: string | vscode.LanguageModelChatRequestMessage, _token: vscode.CancellationToken): Thenable<number> {
		// No real tokenizer available from this endpoint; a char/4 estimate is enough for
		// Agent Mode's budget heuristics, not for exact accounting.
		const asString = typeof text === 'string'
			? text
			: text.content.map(part => (part instanceof vscode.LanguageModelTextPart ? part.value : '')).join('');
		return Promise.resolve(Math.ceil(asString.length / 4));
	}
}
