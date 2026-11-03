// Resolve the `typescript` compiler API to TypeScript 6 for tools that still
// load it (openapi-typescript), mirroring eslint.config.mjs. Next.js keeps
// typechecking with TypeScript 7; this hook only runs inside gen:api.
import { createRequire, registerHooks } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const ts6Root = path.dirname(require.resolve("@typescript/typescript6/package.json"));

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
