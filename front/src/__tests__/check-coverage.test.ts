import { describe, expect, it } from "bun:test";

import {
    CoverageGateError,
    evaluateCoverage,
    parseLcov,
    parseThreshold,
} from "../../scripts/check-coverage-parse.mjs";

type LcovRecord = { source: string; lf: number; lh: number };

function lcov(records: LcovRecord[], newline = "\n"): string {
    return records
        .map(({ source, lf, lh }) =>
            ["TN:", `SF:${source}`, `LF:${lf}`, `LH:${lh}`, "end_of_record"].join(newline),
        )
        .join(newline);
}

function messageOf(run: () => unknown): string {
    try {
        run();
    } catch (error) {
        return error instanceof Error ? error.message : String(error);
    }
    throw new Error("Expected the call to throw");
}

describe("parseThreshold", () => {
    it("accepts finite numbers in (0, 100]", () => {
        for (const raw of [65, 0.5, 100, "65", " 42 ", "1e2"]) {
            expect(Number.isFinite(parseThreshold(raw))).toBe(true);
        }
        expect(parseThreshold(65)).toBe(65);
        expect(parseThreshold("100")).toBe(100);
    });

    it("rejects NaN, infinities, empty strings, negatives, zero and values above 100", () => {
        for (const raw of [
            NaN,
            "NaN",
            Infinity,
            -Infinity,
            "Infinity",
            "",
            "   ",
            "-1",
            "0",
            0,
            "101",
            undefined,
            null,
        ]) {
            expect(() => parseThreshold(raw)).toThrow(CoverageGateError);
        }
    });

    it("gives an actionable diagnostic that names the bad input", () => {
        const message = messageOf(() => parseThreshold("NaN"));
        expect(message).toContain("Invalid coverage threshold");
        expect(message).toContain("finite number greater than 0 and at most 100");
    });
});

describe("parseLcov", () => {
    it("parses a single well-formed record", () => {
        expect(parseLcov(lcov([{ source: "src/a.ts", lf: 10, lh: 8 }]))).toEqual({
            linesFound: 10,
            linesHit: 8,
            files: 1,
        });
    });

    it("aggregates all source records, not just the first one", () => {
        const report = lcov([
            { source: "src/a.ts", lf: 10, lh: 8 },
            { source: "src/b.ts", lf: 5, lh: 1 },
            { source: "src/c.ts", lf: 5, lh: 0 },
        ]);
        expect(parseLcov(report)).toEqual({ linesFound: 20, linesHit: 9, files: 3 });
    });

    it("handles CRLF reports identically to LF reports", () => {
        const records = [
            { source: "src\\a.ts", lf: 10, lh: 8 },
            { source: "src\\b.ts", lf: 5, lh: 1 },
        ];
        expect(parseLcov(lcov(records, "\r\n"))).toEqual(parseLcov(lcov(records, "\n")));
    });

    it("accepts a valid zero-hit record", () => {
        expect(parseLcov(lcov([{ source: "src/a.ts", lf: 10, lh: 0 }]))).toEqual({
            linesFound: 10,
            linesHit: 0,
            files: 1,
        });
    });

    it("flushes a trailing record without end_of_record", () => {
        const report = ["SF:src/a.ts", "LF:4", "LH:4"].join("\n");
        expect(parseLcov(report)).toEqual({ linesFound: 4, linesHit: 4, files: 1 });
    });

    it("rejects empty reports", () => {
        for (const report of ["", "\n", "   ", "TN:\n"]) {
            expect(() => parseLcov(report)).toThrow(CoverageGateError);
        }
        expect(messageOf(() => parseLcov(""))).toContain("Empty coverage report");
    });

    it("rejects malformed, negative, fractional and NaN counters", () => {
        for (const counter of [
            "NaN",
            "-1",
            "1.5",
            "1e3",
            "",
            "+4",
            "0x10",
            "999999999999999999999999",
        ]) {
            const report = lcov([{ source: "src/a.ts", lf: 3, lh: 3 }]).replace(
                "LF:3",
                `LF:${counter}`,
            );
            expect(() => parseLcov(report)).toThrow(CoverageGateError);
        }
        const nanHit = lcov([{ source: "src/a.ts", lf: 3, lh: 3 }]).replace("LH:3", "LH:NaN");
        expect(() => parseLcov(nanHit)).toThrow(CoverageGateError);
    });

    it("rejects LH greater than LF in any record", () => {
        const report = lcov([
            { source: "src/a.ts", lf: 10, lh: 8 },
            { source: "src/b.ts", lf: 3, lh: 4 },
        ]);
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("LH (4) exceeds LF (3)");
        expect(message).toContain("src/b.ts");
    });

    it("rejects records where LF and LH do not appear together", () => {
        const onlyLf = lcov([{ source: "src/a.ts", lf: 3, lh: 3 }]).replace("LH:3\n", "");
        const onlyLh = lcov([{ source: "src/a.ts", lf: 3, lh: 3 }]).replace("LF:3\n", "");
        const noCounters = ["SF:src/a.ts", "end_of_record"].join("\n");
        const trailingLf = ["SF:src/a.ts", "LF:3"].join("\n");
        for (const report of [onlyLf, onlyLh, noCounters, trailingLf]) {
            expect(() => parseLcov(report)).toThrow(CoverageGateError);
        }
        expect(messageOf(() => parseLcov(onlyLf))).toContain("LF and LH must appear together");
    });

    it("rejects a duplicate LF counter instead of accepting the last value", () => {
        // LF:100 / LF:10 / LH:10 used to be accepted as a 10/10 record.
        const report = ["SF:src/a.ts", "LF:100", "LF:10", "LH:10", "end_of_record"].join("\n");
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("Duplicate LF");
        expect(message).toContain("src/a.ts");
    });

    it("rejects a duplicate LH counter", () => {
        const report = ["SF:src/a.ts", "LF:10", "LH:10", "LH:0", "end_of_record"].join("\n");
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("Duplicate LH");
        expect(message).toContain("src/a.ts");
    });

    it("rejects LF after LH as an out-of-order duplicate", () => {
        const report = ["SF:src/a.ts", "LF:10", "LH:8", "LF:10", "end_of_record"].join("\n");
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("Duplicate LF");
        expect(message).toContain("LH");
    });

    it("rejects LH before LF", () => {
        const report = ["SF:src/a.ts", "LH:8", "LF:10", "end_of_record"].join("\n");
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("LH requires a preceding LF");
        expect(message).toContain("src/a.ts");
    });

    it("rejects a second SF before the preceding record is finalized", () => {
        const report = [
            "SF:src/a.ts",
            "LF:10",
            "LH:8",
            "SF:src/b.ts",
            "LF:5",
            "LH:5",
            "end_of_record",
        ].join("\n");
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("src/a.ts");
        expect(message).toContain("end_of_record");
    });

    it("rejects an aggregate LF sum beyond Number.MAX_SAFE_INTEGER", () => {
        const report = lcov([
            { source: "src/a.ts", lf: Number.MAX_SAFE_INTEGER, lh: 0 },
            { source: "src/b.ts", lf: 1, lh: 0 },
        ]);
        const message = messageOf(() => parseLcov(report));
        expect(message).toContain("Number.MAX_SAFE_INTEGER");
        expect(message).toContain("LF");
    });
});

describe("evaluateCoverage", () => {
    it("passes at or above the threshold and fails below it", () => {
        expect(evaluateCoverage({ linesFound: 10, linesHit: 8, threshold: 65 })).toEqual({
            percent: 80,
            passed: true,
        });
        expect(evaluateCoverage({ linesFound: 10, linesHit: 6, threshold: 65 })).toEqual({
            percent: 60,
            passed: false,
        });
    });

    it("treats an exact threshold as a pass", () => {
        expect(evaluateCoverage({ linesFound: 100, linesHit: 65, threshold: 65 }).passed).toBe(
            true,
        );
    });

    it("computes the percentage from synthetic LCOV totals — no live baseline is claimed", () => {
        // Synthetic counts exercise the arithmetic and the 65% boundary; a
        // repository coverage measurement belongs to the CI lcov gate.
        const totals = parseLcov(lcov([{ source: "src/a.ts", lf: 10000, lh: 7738 }]));
        const { percent, passed } = evaluateCoverage({ ...totals, threshold: 65 });
        expect(percent).toBeCloseTo(77.38, 2);
        expect(passed).toBe(true);
    });

    it("rejects invalid totals instead of letting NaN slip through the comparison", () => {
        // Regression: `NaN < threshold` is false, which used to produce a false pass.
        for (const totals of [
            { linesFound: NaN, linesHit: 10, threshold: 65 },
            { linesFound: 10, linesHit: NaN, threshold: 65 },
            { linesFound: 10, linesHit: 10, threshold: NaN },
            { linesFound: 0, linesHit: 0, threshold: 65 },
            { linesFound: 10, linesHit: -1, threshold: 65 },
            { linesFound: 10, linesHit: 11, threshold: 65 },
        ]) {
            expect(() => evaluateCoverage(totals)).toThrow(CoverageGateError);
        }
    });

    it("rejects a NaN threshold even when totals are valid", () => {
        expect(
            messageOf(() => evaluateCoverage({ linesFound: 10, linesHit: 10, threshold: NaN })),
        ).toContain("Invalid coverage threshold");
    });
});
