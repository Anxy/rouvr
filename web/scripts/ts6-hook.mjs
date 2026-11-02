// Node --import hook: redirect "typescript" imports to @typescript/typescript6.
// openapi-typescript uses the classic compiler API (ts.factory), which
// TypeScript 7 does not ship. Same approach as web/eslint.config.mjs.
// Used by `pnpm gen:api` (see web/package.json).
import { createRequire, registerHooks } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const ts6Root = path.dirname(
  require.resolve("@typescript/typescript6/package.json"),
);

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier !== "typescript" && !specifier.startsWith("typescript/")) {
      return nextResolve(specifier, context);
    }
    const parent = context.parentURL ?? "";
    if (
      parent.includes("/@typescript/typescript6/") ||
      parent.includes("/node_modules/typescript/")
    ) {
      return nextResolve(specifier, context);
    }
    const target =
      specifier === "typescript"
        ? require.resolve("@typescript/typescript6")
        : path.join(ts6Root, specifier.slice("typescript/".length));
    return { url: pathToFileURL(target).href, shortCircuit: true };
  },
});
