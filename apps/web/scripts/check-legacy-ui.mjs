import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { deprecatedClasses, deprecatedSelectors } from "../legacy-ui-policy.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const sourceExtensions = new Set([".css", ".js", ".jsx", ".ts", ".tsx"]);

async function collectFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return collectFiles(target);
    return sourceExtensions.has(path.extname(entry.name)) ? [target] : [];
  }));
  return nested.flat();
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const findings = [];
for (const file of await collectFiles(root)) {
  const source = await readFile(file, "utf8");
  const relative = path.relative(root, file).replaceAll("\\", "/");
  const lines = source.split(/\r?\n/);
  for (const className of deprecatedClasses) {
    const pattern = new RegExp(`(?<![-_a-zA-Z0-9])${escapeRegExp(className)}(?![-_a-zA-Z0-9])`);
    lines.forEach((line, index) => {
      if (pattern.test(line)) findings.push(`${relative}:${index + 1}: deprecated class "${className}"`);
    });
  }
  if (file.endsWith(".css")) {
    const compact = source.replace(/\s+/g, " ");
    for (const selector of deprecatedSelectors) {
      if (compact.includes(selector)) findings.push(`${relative}: deprecated selector "${selector}"`);
    }
  }
}

if (findings.length) {
  console.error("Legacy UI policy violations:\n" + findings.join("\n"));
  process.exit(1);
}

console.log(`Legacy UI policy passed: ${deprecatedClasses.length} classes and ${deprecatedSelectors.length} selectors are absent from src.`);
