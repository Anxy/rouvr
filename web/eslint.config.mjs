import { createRequire, registerHooks } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

// typescript-eslint still loads the TypeScript compiler API, which TypeScript 7
// does not ship. Resolve those imports to the TypeScript 6 API. Next.js keeps
// typechecking with TypeScript 7 because this hook only runs inside ESLint.
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

    return {
      url: pathToFileURL(target).href,
      shortCircuit: true,
    };
  },
});

const nextCoreWebVitals = (await import("eslint-config-next/core-web-vitals"))
  .default;
const nextTypescript = (await import("eslint-config-next/typescript")).default;

const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
    ],
  },
];

export default eslintConfig;
