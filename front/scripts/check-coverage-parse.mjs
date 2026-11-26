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
 * Parse an LCOV report (LF/CRLF) into aggregate line totals.
 *
 * Every source record is validated on its own: LF/LH must be non-negative
 * integers and LH must not exceed LF. All records are then summed. Empty or
 * malformed reports throw CoverageGateError.
 */
export function parseLcov(text) {
    if (typeof text !== "string" || text.trim() === "") {
        throw new CoverageGateError("Empty coverage report: no LCOV records found");
    }

    let linesFound = 0;
    let linesHit = 0;
    let files = 0;
    let source = "<unknown>";
    let recordLf;
    let recordLh;

    const finalize = () => {
        if (recordLf === undefined && recordLh === undefined) return;
        const where = `source ${source}`;
        if (recordLf === undefined || recordLh === undefined) {
            throw new CoverageGateError(
                `Malformed coverage record for ${where}: LF and LH must appear together`,
            );
        }
        if (recordLh > recordLf) {
            throw new CoverageGateError(
                `Invalid coverage record for ${where}: LH (${recordLh}) exceeds LF (${recordLf})`,
            );
        }
        linesFound += recordLf;
        linesHit += recordLh;
        files += 1;
        recordLf = undefined;
        recordLh = undefined;
    };

    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (line.startsWith("SF:")) {
            source = line.slice(3).trim() || "<unknown>";
        } else if (line.startsWith("LF:")) {
            recordLf = parseCount(line.slice(3), "LF", `source ${source}`);
        } else if (line.startsWith("LH:")) {
            recordLh = parseCount(line.slice(3), "LH", `source ${source}`);
        } else if (line === "end_of_record") {
            finalize();
            source = "<unknown>";
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
