import { deprecatedClasses } from "./legacy-ui-policy.mjs";

const escaped = deprecatedClasses.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

export default {
  ignoreFiles: ["dist/**", "node_modules/**"],
  rules: {
    "selector-disallowed-list": [
      ...escaped.map((name) => `/\\.${name}(?![-_a-zA-Z0-9])/`),
      "/\\.provider-form\\s*>\\s*footer/",
    ],
  },
};
