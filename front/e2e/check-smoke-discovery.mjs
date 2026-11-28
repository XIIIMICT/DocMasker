/**
 * Single source of truth for the CI smoke discovery guard.
 *
 * Usage: bun e2e/check-smoke-discovery.mjs <playwright --list output file>
 *
 * Exact spec basenames are read from the `[smoke] › file.spec.ts:line:col ›`
 * lines, so `smoke.spec.ts` can no longer be satisfied by
 * `rerender-smoke.spec.ts` or `hover-smoke.spec.ts`. A minimum test count also
 * prevents a broken `testMatch` from quietly shrinking the project.
 */
import { readFileSync } from "node:fs";

const REQUIRED_SPECS = [
    "health.spec.ts",
    "smoke.spec.ts",
    "rerender-smoke.spec.ts",
    "hover-smoke.spec.ts",
];
// 41 tests are listed on this branch (measured 2026-11-28): 29 main smoke,
// 10 hover, 1 rerender, 1 health. The 34-total floor stops a whole smoke or
// hover spec from dropping out; the 20-main floor stops smoke.spec.ts from
// shrinking; the exact basenames above catch a rename of a smaller spec.
const MIN_TOTAL_TESTS = 34;
const MIN_MAIN_SPEC_TESTS = 20;
// Match the `file.spec.ts:line:col` token itself rather than Playwright's
// `›` separator: PowerShell redirects can mojibake that glyph on Windows.
const TEST_LINE = /(?:^|\s)(\S+?\.spec\.ts):\d+:\d+(?:\s|$)/;

const listFile = process.argv[2];
if (!listFile) {
    console.error("Usage: bun e2e/check-smoke-discovery.mjs <playwright --list output file>");
    process.exit(2);
}

const bytes = readFileSync(listFile);
// Windows PowerShell `>` writes UTF-16LE; CI's bash writes UTF-8. Accept both
// so the same guard and negative proof work in either shell.
const listing =
    bytes[0] === 0xff && bytes[1] === 0xfe ? bytes.toString("utf16le") : bytes.toString("utf8");
const lines = listing.split(/\r?\n/);
const counts = new Map();
let total = 0;
for (const line of lines) {
    const match = TEST_LINE.exec(line);
    if (!match) continue;
    const spec = match[1];
    counts.set(spec, (counts.get(spec) ?? 0) + 1);
    total += 1;
}

const problems = [];
for (const spec of REQUIRED_SPECS) {
    if (!counts.has(spec)) problems.push(`missing smoke spec: ${spec}`);
}
const mainTests = counts.get("smoke.spec.ts") ?? 0;
if (mainTests < MIN_MAIN_SPEC_TESTS) {
    problems.push(
        `smoke.spec.ts lists ${mainTests} tests, expected at least ${MIN_MAIN_SPEC_TESTS}`,
    );
}
if (total < MIN_TOTAL_TESTS) {
    problems.push(`smoke project lists ${total} tests, expected at least ${MIN_TOTAL_TESTS}`);
}

if (problems.length > 0) {
    console.error(`Smoke discovery guard failed:\n${problems.map((p) => `  - ${p}`).join("\n")}`);
    process.exit(1);
}
console.log(
    `Smoke discovery guard: ${total} tests across ${counts.size} specs; required specs present.`,
);
