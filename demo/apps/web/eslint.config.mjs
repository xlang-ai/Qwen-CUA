import { createRequire } from "node:module";
import { dirname } from "node:path";
import { FlatCompat } from "@eslint/eslintrc";

const require = createRequire(import.meta.url);

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
  resolvePluginsRelativeTo: dirname(
    require.resolve("eslint-config-next/package.json"),
  ),
});

const config = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "lib/api-schema.d.ts",
      "next-env.d.ts",
    ],
  },
];

export default config;
