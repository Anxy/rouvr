// Generate web/src/lib/api/schema.d.ts from web/src/lib/api/openapi.json
// using the real openapi-typescript CLI, preloading the TS6 compiler-API
// shim (see ts6-hook.mjs) because TypeScript 7 does not ship the compiler
// API. Re-export the snapshot first: `pnpm --filter web openapi:export`.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.dirname(here);
const require = createRequire(import.meta.url);
const pkgRoot = path.dirname(require.resolve("openapi-typescript/package.json"));
const cli = path.join(pkgRoot, "bin", "cli.js");
const hook = path.join(here, "ts6-hook.mjs");

const result = spawnSync(
  process.execPath,
  [
    "--import",
    hook,
    cli,
    "./src/lib/api/openapi.json",
    "-o",
    "./src/lib/api/schema.d.ts",
  ],
  { cwd: webRoot, stdio: "inherit" },
);
process.exit(result.status ?? 1);
