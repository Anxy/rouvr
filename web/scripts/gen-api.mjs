// Run openapi-typescript against the TypeScript 6 compiler API.
//
// openapi-typescript builds output with ts.factory, which the TypeScript 7
// native port does not ship (same reason eslint.config.mjs shims TS6 for
// typescript-eslint). This maps bare "typescript" imports to the
// @typescript/typescript6 package, then hands off to the real CLI with
// process.argv untouched.
import { createRequire, registerHooks } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const ts6Main = require.resolve("@typescript/typescript6");
const ts6Root = path.dirname(
  require.resolve("@typescript/typescript6/package.json"),
);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier !== "typescript" && !specifier.startsWith("typescript/")) {
      return nextResolve(specifier, context);
    }
    const target =
      specifier === "typescript"
        ? ts6Main
        : path.join(ts6Root, specifier.slice("typescript/".length));
    return { url: pathToFileURL(target).href, shortCircuit: true };
  },
});

const cliDir = path.dirname(
  require.resolve("openapi-typescript/package.json"),
);

await import(pathToFileURL(path.join(cliDir, "bin", "cli.js")).href);
