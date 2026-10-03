import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(webRoot, "src");
const themeSource = await readFile(path.join(sourceRoot, "theme/appTheme.ts"), "utf8");
const defined = new Set(themeSource.match(/--app-[a-z0-9-]+/g) ?? []);

async function cssFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return cssFiles(target);
    return entry.name.endsWith(".css") ? [target] : [];
  }));
  return nested.flat();
}

const missing = new Map();
for (const file of await cssFiles(sourceRoot)) {
  const source = await readFile(file, "utf8");
  for (const variable of source.match(/var\((--app-[a-z0-9-]+)/g) ?? []) {
    const name = variable.slice(4);
    if (!defined.has(name)) {
      const references = missing.get(name) ?? [];
      references.push(path.relative(sourceRoot, file).replaceAll("\\", "/"));
      missing.set(name, references);
    }
  }
}

const sharedUi = await readFile(path.join(sourceRoot, "components/ui/ui.css"), "utf8");
const forbiddenSharedColors = [...new Set(sharedUi.match(/#[0-9a-fA-F]{3,8}\b/g) ?? [])]
  .filter((color) => color.toLowerCase() !== "#fff");

if (missing.size || forbiddenSharedColors.length) {
  if (missing.size) {
    console.error("Undefined app theme variables:");
    for (const [name, files] of missing) console.error(`- ${name}: ${[...new Set(files)].join(", ")}`);
  }
  if (forbiddenSharedColors.length) {
    console.error(`Shared UI contains non-semantic colors: ${forbiddenSharedColors.join(", ")}`);
  }
  process.exit(1);
}

console.log(`Theme contract passed: ${defined.size} app variables cover every CSS reference; shared UI has no non-semantic hex colors.`);
