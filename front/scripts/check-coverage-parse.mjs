/**
 * Pure parsing/validation for the frontend coverage gate.
 *
 * No I/O and no process.exit here: every failure is reported by throwing a
 * CoverageGateError with an actionable message, so the same rules can be
 * exercised by unit tests. The executable wrapper stays thin (see
 * check-coverage.mjs).
 */

/** Error type carrying a concise, actionable gate diagnostic. */
export class CoverageGateError extends Error {
    constructor(message) {
        super(message);
        this.name = "CoverageGateError";
    }
}

const THRESHOLD_HINT = "expected a finite number greater than 0 and at most 100";

/**
 * Validate a coverage threshold. Accepts numbers and numeric strings.
 * Returns a finite number in (0, 100]; throws CoverageGateError otherwise.
 * Rejects NaN, infinities, empty strings, negatives, zero and values above 100.
 */
export function parseThreshold(raw) {
    const blank =
        raw === undefined || raw === null || (typeof raw === "string" && raw.trim() === "");
    if (blank) {
        throw new CoverageGateError(
            `Missing coverage threshold: ${THRESHOLD_HINT} (got ${JSON.stringify(raw)})`,
        );
    }

    const value = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(value) || value <= 0 || value > 100) {
        throw new CoverageGateError(
            `Invalid coverage threshold ${JSON.stringify(raw)}: ${THRESHOLD_HINT}`,
        );
    }
    return value;
}

/**
 * Parse one LF/LH counter. Only plain non-negative integer digits are accepted
 * (no sign, decimal point, exponent, blank or NaN).
 */
function parseCount(value, label, context) {
    const trimmed = typeof value === "string" ? value.trim() : "";
    if (!/^\d+$/.test(trimmed)) {
        throw new CoverageGateError(
            `Invalid ${label} count ${JSON.stringify(value)} in ${context}: expected a non-negative integer`,
        );
    }
    const count = Number(trimmed);
    if (!Number.isSafeInteger(count)) {
        throw new CoverageGateError(
            `Invalid ${label} count ${JSON.stringify(value)} in ${context}: expected a non-negative integer`,
        );
    }
    return count;
}

/**
 * Add a validated record count to an aggregate. Two individually safe counts
 * can still sum past Number.MAX_SAFE_INTEGER, so each addition is checked.
 */
function addCount(total, count, label, source) {
    const sum = total + count;
    if (!Number.isSafeInteger(sum)) {
        throw new CoverageGateError(
            `Unsafe coverage total for source ${source}: ${label} sum (${total} + ${count}) exceeds Number.MAX_SAFE_INTEGER`,
        );
    }
    return sum;
}

/**
 * Parse an LCOV report (LF/CRLF) into aggregate line totals.
 *
 * Each source record is a strict state machine: SF must be followed by exactly
 * one LF, then exactly one LH, then end_of_record before the next SF. Any
 * duplicate, out-of-order, incomplete, inconsistent or unsafe counter throws
 * CoverageGateError; unrelated LCOV tokens (TN, DA, FN, ...) are ignored.
 */
export function parseLcov(text) {
    if (typeof text !== "string" || text.trim() === "") {
        throw new CoverageGateError("Empty coverage report: no LCOV records found");
    }

    const NO_RECORD = "none";
    const EXPECT_LF = "expect-lf";
    const EXPECT_LH = "expect-lh";
    const COMPLETE = "complete";

    let linesFound = 0;
    let linesHit = 0;
    let files = 0;
    let source = "<unknown>";
    let state = NO_RECORD;
    let recordLf;
    let recordLh;

    const incomplete = () =>
        new CoverageGateError(
            `Malformed coverage record for source ${source}: LF and LH must appear together`,
        );

    const finalize = () => {
        if (state === COMPLETE) {
            if (recordLh > recordLf) {
                throw new CoverageGateError(
                    `Invalid coverage record for source ${source}: LH (${recordLh}) exceeds LF (${recordLf})`,
                );
            }
            linesFound = addCount(linesFound, recordLf, "LF", source);
            linesHit = addCount(linesHit, recordLh, "LH", source);
            files += 1;
        } else if (state !== NO_RECORD) {
            throw incomplete();
        }
        state = NO_RECORD;
        source = "<unknown>";
        recordLf = undefined;
        recordLh = undefined;
    };

    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (line.startsWith("SF:")) {
            if (state !== NO_RECORD) {
                throw new CoverageGateError(
                    `Malformed coverage record for source ${source}: the previous record must be finalized with end_of_record before the next SF`,
                );
            }
            source = line.slice(3).trim() || "<unknown>";
            state = EXPECT_LF;
        } else if (line.startsWith("LF:")) {
            if (state === NO_RECORD) {
                throw new CoverageGateError(
                    `Unexpected LF counter for source ${source}: SF must appear before LF`,
                );
            }
            if (state !== EXPECT_LF) {
                throw new CoverageGateError(
                    `Duplicate LF counter for source ${source}: LF must appear exactly once before LH`,
                );
            }
            recordLf = parseCount(line.slice(3), "LF", `source ${source}`);
            state = EXPECT_LH;
        } else if (line.startsWith("LH:")) {
            if (state === NO_RECORD) {
                throw new CoverageGateError(
                    `Unexpected LH counter for source ${source}: SF must appear before LH`,
                );
            }
            if (state === EXPECT_LF) {
                throw new CoverageGateError(
                    `Malformed coverage record for source ${source}: LH requires a preceding LF`,
                );
            }
            if (state === COMPLETE) {
                throw new CoverageGateError(
                    `Duplicate LH counter for source ${source}: LH must appear exactly once after LF`,
                );
            }
            recordLh = parseCount(line.slice(3), "LH", `source ${source}`);
            state = COMPLETE;
        } else if (line === "end_of_record") {
            if (state === NO_RECORD) {
                throw new CoverageGateError("Unexpected end_of_record without an open SF record");
            }
            finalize();
        }
    }
    finalize(); // flush a trailing record that has no explicit end_of_record

    if (files === 0 || linesFound === 0) {
        throw new CoverageGateError("Empty coverage report: no line counts (LF/LH) found");
    }
    return { linesFound, linesHit, files };
}

/**
 * Decide pass/fail from validated totals and threshold.
 * Guards against non-finite or out-of-range inputs so a NaN can never slip
 * through the comparison and produce a false pass (`NaN < threshold` is false).
 */
export function evaluateCoverage({ linesFound, linesHit, threshold }) {
    if (!Number.isSafeInteger(linesFound) || linesFound <= 0) {
        throw new CoverageGateError(
            `Invalid coverage totals: LF must sum to a positive integer (got ${linesFound})`,
        );
    }
    if (!Number.isSafeInteger(linesHit) || linesHit < 0 || linesHit > linesFound) {
        throw new CoverageGateError(
            `Invalid coverage totals: LH must be an integer between 0 and LF (got ${linesHit}/${linesFound})`,
        );
    }
    if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 100) {
        throw new CoverageGateError(`Invalid coverage threshold: ${THRESHOLD_HINT}`);
    }

    const percent = (linesHit / linesFound) * 100;
    if (!Number.isFinite(percent)) {
        throw new CoverageGateError("Coverage calculation produced a non-finite percentage");
    }
    return { percent, passed: percent >= threshold };
}
