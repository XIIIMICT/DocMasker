/**
 * Fail when line coverage from bun's lcov report drops below the threshold.
 *
 * `bun test` has no --coverage-fail-under flag, so CI enforces the gate here
 * (stdlib only, no dependencies). Usage:
 *   bun test --coverage --coverage-reporter=lcov
 *   bun scripts/check-coverage.mjs [threshold] [lcov path]
 */

import { readFileSync } from "node:fs";

const threshold = Number(process.argv[2] ?? 65);
const lcovPath = process.argv[3] ?? "coverage/lcov.info";

const text = readFileSync(lcovPath, "utf8");
let linesFound = 0;
let linesHit = 0;
for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("LF:")) linesFound += Number(line.slice(3));
    else if (line.startsWith("LH:")) linesHit += Number(line.slice(3));
}

if (linesFound === 0) {
    console.error(`No coverage lines found in ${lcovPath}`);
    process.exit(1);
}

const percent = (linesHit / linesFound) * 100;
console.log(
    `Line coverage: ${percent.toFixed(2)}% (${linesHit}/${linesFound}), threshold ${threshold}%`,
);
if (percent < threshold) {
    console.error(`Coverage ${percent.toFixed(2)}% is below ${threshold}%`);
    process.exit(1);
}
