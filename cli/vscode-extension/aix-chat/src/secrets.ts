/*---------------------------------------------------------------------------------------------
 *  Client-side secret redaction before prompts leave the extension.
 *  Backend must still scrub; this is defense-in-depth for prompts / objective text.
 *--------------------------------------------------------------------------------------------*/

const PATTERNS: { name: string; re: RegExp }[] = [
	{ name: 'aws_access_key', re: /\bAKIA[0-9A-Z]{16}\b/g },
	{ name: 'aws_secret', re: /\b(?:aws)?_?secret(?:_access)?_?key\s*[=:]\s*['"]?[A-Za-z0-9\/+=]{30,}['"]?/gi },
	{ name: 'generic_api_key', re: /\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|private[_-]?key)\s*[=:]\s*['"]?[^\s'"]{12,}['"]?/gi },
	{ name: 'bearer', re: /\bBearer\s+[A-Za-z0-9\-._~+\/]+=*/gi },
	{ name: 'jwt', re: /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g },
	{ name: 'private_key_block', re: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g },
	{ name: 'connection_string', re: /\b(?:postgres|mysql|mongodb|redis):\/\/[^\s'"]+/gi },
	// `\b` before "password"/"secret" fails on env-var-style names like `SMTP_PASSWORD=`
	// because `_` and the following letter are both "word" characters, so no boundary
	// exists between them. `(?:^|[^A-Za-z0-9])` matches start-of-string or any
	// non-alphanumeric separator (including `_`) instead, so `SMTP_PASSWORD=`,
	// `DB_PASSWORD:`, `MY-API-SECRET=` etc. are all caught too.
	{ name: 'password_assign', re: /(?:^|[^A-Za-z0-9])(?:password|secret)\s*[=:]\s*['"]?[^\s'"]{4,}['"]?/gi },
];

export interface RedactionResult {
	text: string;
	redacted: boolean;
	kinds: string[];
}

export function redactSecrets(input: string): RedactionResult {
	let text = input || '';
	const kinds: string[] = [];
	for (const p of PATTERNS) {
		if (p.re.test(text)) {
			kinds.push(p.name);
			p.re.lastIndex = 0;
			text = text.replace(p.re, `[REDACTED:${p.name}]`);
		}
		p.re.lastIndex = 0;
	}
	return { text, redacted: kinds.length > 0, kinds };
}
