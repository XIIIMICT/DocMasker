/**
 * Fail when line coverage from bun's lcov report drops below the threshold.
 *
 * `bun test` has no --coverage-fail-under flag, so CI enforces the gate here
 * (stdlib only, no dependencies). Usage:
 *   bun test --coverage --coverage-reporter=lcov
 *   bun scripts/check-coverage.mjs [threshold] [lcov path]
 *
 * All parsing/validation lives in the pure check-coverage-parse.mjs module;
 * this wrapper only reads the file, maps errors to concise diagnostics and
 * sets the exit code.
 */

import { readFileSync } from "node:fs";

import {
    CoverageGateError,
    evaluateCoverage,
    parseLcov,
    parseThreshold,
} from "./check-coverage-parse.mjs";

const DEFAULT_THRESHOLD = 65;
const DEFAULT_LCOV_PATH = "coverage/lcov.info";

function fail(message) {
    console.error(message);
    return 1;
}

function main() {
    let threshold;
    try {
        threshold = parseThreshold(process.argv[2] ?? DEFAULT_THRESHOLD);
    } catch (error) {
        return fail(
            error instanceof CoverageGateError ? error.message : "Invalid coverage threshold",
        );
    }

    const lcovPath = process.argv[3] ?? DEFAULT_LCOV_PATH;
    let text;
    try {
        text = readFileSync(lcovPath, "utf8");
    } catch (error) {
        const code =
            error !== null && typeof error === "object" && "code" in error
                ? String(error.code)
                : "";
        return fail(`Unable to read coverage report "${lcovPath}"${code ? ` (${code})` : ""}`);
    }

    let totals;
    try {
        totals = parseLcov(text);
    } catch (error) {
        return fail(
            error instanceof CoverageGateError
                ? error.message
                : `Malformed coverage report in "${lcovPath}"`,
        );
    }

    let decision;
    try {
        decision = evaluateCoverage({ ...totals, threshold });
    } catch (error) {
        return fail(error instanceof CoverageGateError ? error.message : "Invalid coverage data");
    }

    const { percent, passed } = decision;
    console.log(
        `Line coverage: ${percent.toFixed(2)}% (${totals.linesHit}/${totals.linesFound}), threshold ${threshold}%`,
    );
    if (!passed) {
        return fail(`Coverage ${percent.toFixed(2)}% is below ${threshold}%`);
    }
    return 0;
}

process.exitCode = main();
