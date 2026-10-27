import * as esbuild from 'esbuild';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes('--watch');

const options = {
	entryPoints: [path.join(root, 'src', 'extension.ts')],
	bundle: true,
	outfile: path.join(root, 'out', 'extension.js'),
	external: ['vscode'],
	format: 'cjs',
	platform: 'node',
	sourcemap: true,
	logLevel: 'info',
};

if (watch) {
	const ctx = await esbuild.context(options);
	await ctx.watch();
} else {
	await esbuild.build(options);
}
