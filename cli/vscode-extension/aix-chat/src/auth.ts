/*---------------------------------------------------------------------------------------------
 *  AIX auth manager — performs the same POST /login hyeve-auth-service call AIX-Frontend
 *  itself uses (see AIX-Frontend/src/app/api/auth/login/route.ts), stores the resulting
 *  access/refresh tokens in VS Code's encrypted SecretStorage (never in settings.json or in
 *  plain sight), and refreshes the access token automatically via POST /refresh before it
 *  expires. This replaces copying the aix_v2_access_token cookie out of a browser by hand.
 *--------------------------------------------------------------------------------------------*/
import * as crypto from 'crypto';
import * as http from 'http';
import * as https from 'https';
import * as vscode from 'vscode';

const SECRET_KEY = 'aix.session';
// hyeve-auth-service mounts its auth router under this fixed prefix
// (services/hyeve-auth-service/app/settings.py: api_prefix = "/v1/auth").
const AUTH_PREFIX = '/v1/auth';

interface StoredSession {
	accessToken: string;
	refreshToken: string;
	tenantId: string;
	username: string;
	expiresAt: number;
}

interface TokenResponsePayload {
	access_token?: string;
	refresh_token?: string;
	expires_in?: number;
	detail?: string;
	message?: string;
	code?: string;
}

function postJson(baseUrl: string, path: string, headers: Record<string, string>, body: unknown): Promise<{ status: number; json: TokenResponsePayload }> {
	return new Promise((resolve, reject) => {
		let url: URL;
		try {
			url = new URL(path, baseUrl);
		} catch (err) {
			reject(err);
			return;
		}
		const payload = Buffer.from(JSON.stringify(body), 'utf8');
		const transport = url.protocol === 'https:' ? https : http;
		const req = transport.request(url, {
			method: 'POST',
			headers: { ...headers, 'Content-Type': 'application/json', 'Content-Length': payload.length },
			timeout: 30_000,
		}, res => {
			const chunks: Buffer[] = [];
			res.on('data', chunk => chunks.push(chunk));
			res.on('end', () => {
				const text = Buffer.concat(chunks).toString('utf8');
				let json: TokenResponsePayload = {};
				try {
					json = text ? JSON.parse(text) : {};
				} catch {
					json = { message: text };
				}
				resolve({ status: res.statusCode ?? 0, json });
			});
		});
		req.on('error', reject);
		req.on('timeout', () => req.destroy(new Error('Request timed out')));
		req.write(payload);
		req.end();
	});
}

export function extractTenantFromJwt(token: string): string | undefined {
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

export class AixAuthManager {
	private session: StoredSession | undefined;
	private readonly statusBarItem: vscode.StatusBarItem;
	private readonly ready: Promise<void>;
	private readonly _onDidChangeAuthState = new vscode.EventEmitter<boolean>();
	public readonly onDidChangeAuthState = this._onDidChangeAuthState.event;

	constructor(private readonly context: vscode.ExtensionContext) {
		this.statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
		this.statusBarItem.command = 'aix.signIn';
		context.subscriptions.push(this.statusBarItem);
		this.updateStatusBar();
		this.ready = this.restore();
	}

	private async restore(): Promise<void> {
		const raw = await this.context.secrets.get(SECRET_KEY);
		if (raw) {
			try {
				this.session = JSON.parse(raw) as StoredSession;
				// Prefer tenant embedded in the JWT so header/cookie stay in sync with the BFF.
				if (this.session.accessToken) {
					const jwtTenant = extractTenantFromJwt(this.session.accessToken);
					if (jwtTenant) this.session.tenantId = jwtTenant;
				}
			} catch {
				this.session = undefined;
			}
		}
		this.updateStatusBar();
	}

	/** Wait until secrets have been loaded from SecretStorage. */
	async whenReady(): Promise<void> {
		await this.ready;
	}

	private async persist(): Promise<void> {
		if (this.session) {
			await this.context.secrets.store(SECRET_KEY, JSON.stringify(this.session));
		} else {
			await this.context.secrets.delete(SECRET_KEY);
		}
		this.updateStatusBar();
	}

	private updateStatusBar(): void {
		if (this.session) {
			this.statusBarItem.text = `$(account) AIX: ${this.session.username}`;
			this.statusBarItem.tooltip = `Signed in to AIX as ${this.session.username} (tenant ${this.session.tenantId}). Click to switch accounts.`;
		} else {
			this.statusBarItem.text = '$(account) AIX: Sign in';
			this.statusBarItem.tooltip = 'Sign in to AIX';
		}
		this.statusBarItem.show();
		this._onDidChangeAuthState.fire(Boolean(this.session?.username));
	}

	async getTenantId(): Promise<string | undefined> {
		await this.ready;
		if (this.session?.accessToken) {
			const jwtTenant = extractTenantFromJwt(this.session.accessToken);
			if (jwtTenant) return jwtTenant;
		}
		return this.session?.tenantId;
	}

	async getUsername(): Promise<string | undefined> {
		await this.ready;
		return this.session?.username;
	}

	async signIn(): Promise<void> {
		await this.ready;
		const config = vscode.workspace.getConfiguration('aix');
		const authUrl = config.get<string>('authUrl', '') || 'http://localhost:8222';
		const username = await vscode.window.showInputBox({ prompt: 'AIX username / email', ignoreFocusOut: true });
		if (!username) {
			return;
		}
		const password = await vscode.window.showInputBox({ prompt: 'AIX password', password: true, ignoreFocusOut: true });
		if (!password) {
			return;
		}
		const defaultTenant = config.get<string>('tenantId', '') || 'hyeve-core';
		const tenantId = (await vscode.window.showInputBox({ prompt: 'Tenant ID', value: defaultTenant, ignoreFocusOut: true })) || defaultTenant;

		let response: { status: number; json: TokenResponsePayload };
		try {
			response = await postJson(authUrl, `${AUTH_PREFIX}/login`, {
				'X-Tenant-ID': tenantId,
				'X-Hyeve-Tenant-Id': tenantId,
			}, { username, password });
		} catch (err) {
			void vscode.window.showErrorMessage(`Could not reach AIX auth service: ${err instanceof Error ? err.message : String(err)}`);
			return;
		}

		if (response.json.code === 'AUTH.MFA_REQUIRED') {
			void vscode.window.showErrorMessage('This account requires MFA, which AIX Studio sign-in does not support yet.');
			return;
		}
		if (!response.json.access_token) {
			void vscode.window.showErrorMessage(`AIX sign-in failed: ${response.json.detail ?? response.json.message ?? `HTTP ${response.status}`}`);
			return;
		}

		this.session = {
			accessToken: response.json.access_token,
			refreshToken: response.json.refresh_token ?? '',
			tenantId,
			username,
			expiresAt: Date.now() + (Number(response.json.expires_in) || 3600) * 1000,
		};
		await this.persist();
		void vscode.window.showInformationMessage(`Signed in to AIX as ${username}.`);
	}

	async signOut(): Promise<void> {
		await this.ready;
		this.session = undefined;
		await this.persist();
		void vscode.window.showInformationMessage('Signed out of AIX.');
	}

	/**
	 * Returns a live access token, transparently refreshing it first if it is expired or
	 * about to expire. Returns undefined if the user isn't signed in, or if the refresh
	 * token itself was rejected (in which case the stored session is cleared so the next
	 * call to signIn() starts clean).
	 */
	async getAccessToken(): Promise<string | undefined> {
		await this.ready;
		if (!this.session) {
			return undefined;
		}
		const aboutToExpire = Date.now() > this.session.expiresAt - 60_000;
		if (!aboutToExpire) {
			return this.session.accessToken;
		}
		if (!this.session.refreshToken) {
			await this.signOut();
			return undefined;
		}
		const config = vscode.workspace.getConfiguration('aix');
		const authUrl = config.get<string>('authUrl', '');
		if (!authUrl) {
			return this.session.accessToken;
		}
		let response: { status: number; json: TokenResponsePayload };
		try {
			response = await postJson(authUrl, `${AUTH_PREFIX}/refresh`, {
				'X-Hyeve-Tenant-Id': this.session.tenantId,
				'Idempotency-Key': crypto.randomUUID(),
			}, { refresh_token: this.session.refreshToken });
		} catch {
			return this.session.accessToken;
		}
		if (!response.json.access_token) {
			await this.signOut();
			return undefined;
		}
		this.session = {
			...this.session,
			accessToken: response.json.access_token,
			refreshToken: response.json.refresh_token ?? this.session.refreshToken,
			expiresAt: Date.now() + (Number(response.json.expires_in) || 3600) * 1000,
		};
		await this.persist();
		return this.session.accessToken;
	}
}
