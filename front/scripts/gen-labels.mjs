/**
 * Generate the shared label catalogue from the backend's canonical
 * `back/config/masking/labels.yaml` (brief §5.3). Run from the repo root:
 *   bun run --cwd front gen:labels
 * CI verifies the generated file is fresh; never edit it by hand.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const yamlPath = fileURLToPath(new URL("../../back/config/masking/labels.yaml", import.meta.url));
const outPath = fileURLToPath(new URL("../src/shared/api/labels.generated.ts", import.meta.url));

const text = readFileSync(yamlPath, "utf8");
const entries = [];
let current = null;
for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith("#") || !line) continue;
    const id = line.match(/^-\s*id:\s*(.+)$/);
    if (id) {
        current = { id: id[1].trim().replace(/^["']|["']$/g, "") };
        continue;
    }
    const title = line.match(/^title:\s*(.+)$/);
    if (title && current) {
        current.title = title[1].trim().replace(/^["']|["']$/g, "");
        if (current.id && current.title) {
            entries.push({ id: current.id, title: current.title });
        }
        current = null;
    }
}
if (entries.length === 0) throw new Error("No labels found in labels.yaml");

const body = [
    "// GENERATED FILE — do not edit. Source: back/config/masking/labels.yaml",
    "// Regenerate with: bun run --cwd front gen:labels",
    "/** Canonical label ids → Russian keys-section titles, in output order. */",
    "export const LABEL_CATALOGUE: ReadonlyArray<{ id: string; title: string }> = [",
    ...entries.map(
        (e) => `    { id: ${JSON.stringify(e.id)}, title: ${JSON.stringify(e.title)} },`,
    ),
    "] as const;",
    "",
    "export const KEYS_SECTION_TITLES: readonly string[] = LABEL_CATALOGUE.map((entry) => entry.title);",
    "",
    "export const KEYS_TITLE_LABELS: Readonly<Record<string, string>> = Object.fromEntries(",
    "    LABEL_CATALOGUE.map((entry) => [entry.title, entry.id]),",
    ");",
    "",
].join("\n");
writeFileSync(outPath, body, "utf8");
console.log(`Generated ${outPath} (${entries.length} labels)`);
