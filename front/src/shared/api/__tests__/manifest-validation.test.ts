import { describe, expect, it } from "bun:test";

import {
    ManifestValidationError,
    parseResultManifest,
    type ManifestOperation,
} from "@/shared/api/manifest-validation";

function validManifest(operation: ManifestOperation): Record<string, unknown> {
    return {
        schema_version: 1,
        operation,
        status: "succeeded",
        complete: true,
        masking_ruleset_version: 1,
        exception_config_digest: "digest",
        input: { sha256: "abc", size_bytes: 10 },
        output:
            operation === "mask_file"
                ? { document_name: "doc.docx", keys_name: "doc.yaml" }
                : { document_name: "doc.docx" },
        statistics:
            operation === "mask_file"
                ? {
                      total_occurrences: 2,
                      occurrences_by_label: { person: 1, email: 1 },
                      unique_by_label: { person: 1, email: 1 },
                  }
                : { unused_keys: ["VALUE-B"] },
        coverage: {
            processed_parts: ["main_document"],
            processed: [{ name: "word/document.xml", category: "main_document", text_nodes: 3 }],
            unprocessed_text_parts: [],
            rejected_parts: [],
            raster_image_ocr: "not_performed",
        },
        cache: { hit: false, namespace_version: 1 },
    };
}

function obj(value: unknown): Record<string, unknown> {
    return value as Record<string, unknown>;
}

function firstPart(raw: Record<string, unknown>): Record<string, unknown> {
    const processed = obj(raw.coverage).processed;
    if (!Array.isArray(processed)) throw new Error("fixture missing processed parts");
    return obj(processed[0]);
}

function text(
    operation: ManifestOperation,
    change?: (manifest: Record<string, unknown>) => void,
): string {
    const manifest = validManifest(operation);
    change?.(manifest);
    return JSON.stringify(manifest);
}

const OPERATIONS: ManifestOperation[] = ["mask_file", "unmask_file"];

function expectInvalid(payload: string, operation: ManifestOperation): string {
    try {
        parseResultManifest(payload, operation);
        throw new Error("expected rejection");
    } catch (error) {
        expect(error).toBeInstanceOf(ManifestValidationError);
        expect((error as ManifestValidationError).code).toBe("manifest_invalid");
        expect((error as Error).message).toBe("Некорректный отчёт о результате.");
        return (error as Error).message;
    }
}

describe("parseResultManifest — valid contracts", () => {
    it("accepts and normalises a current mask_file manifest", () => {
        const manifest = parseResultManifest(text("mask_file"), "mask_file");
        expect(manifest.schema_version).toBe(1);
        expect(manifest.operation).toBe("mask_file");
        expect(manifest.status).toBe("succeeded");
        expect(manifest.statistics.total_occurrences).toBe(2);
        expect(manifest.statistics.occurrences_by_label).toEqual({ person: 1, email: 1 });
        expect(manifest.coverage.processed?.[0]?.text_nodes).toBe(3);
        expect(manifest.cache).toEqual({ hit: false, namespace_version: 1 });
    });

    it("accepts a current unmask_file manifest without occurrence counts", () => {
        const manifest = parseResultManifest(text("unmask_file"), "unmask_file");
        expect(manifest.statistics.unused_keys).toEqual(["VALUE-B"]);
        expect(manifest.statistics.total_occurrences).toBeUndefined();
    });

    it("tolerates unknown extra fields at every level for backward compatibility", () => {
        const manifest = parseResultManifest(
            text("mask_file", (raw) => {
                raw.future_top_level = "ignored";
                obj(raw.input).future_input = 42;
                obj(raw.statistics).unused_keys = ["extra-but-typed"];
                obj(raw.coverage).future_coverage = true;
                firstPart(raw).future_part = "ignored";
            }),
            "mask_file",
        );
        expect(manifest.statistics.total_occurrences).toBe(2);
        // A known field is still type-checked even when it is not operation-required.
        expect(manifest.statistics.unused_keys).toEqual(["extra-but-typed"]);
    });
});

describe("parseResultManifest — complete and coverage consistency", () => {
    it("accepts empty exception arrays with complete true for both operations", () => {
        for (const operation of OPERATIONS) {
            const manifest = parseResultManifest(text(operation), operation);
            expect(manifest.complete).toBe(true);
            expect(manifest.coverage.unprocessed_text_parts).toEqual([]);
            expect(manifest.coverage.rejected_parts).toEqual([]);
        }
    });

    it("tolerates unknown extras for both operations when the known contract is valid", () => {
        for (const operation of OPERATIONS) {
            const manifest = parseResultManifest(
                text(operation, (raw) => {
                    raw.future_field = 1;
                    obj(raw.coverage).future_coverage = "ignored";
                }),
                operation,
            );
            expect(manifest.complete).toBe(true);
            expect(manifest.coverage.unprocessed_text_parts).toEqual([]);
        }
    });

    it("rejects complete false regardless of the coverage arrays", () => {
        const changes: Array<(raw: Record<string, unknown>) => void> = [
            (raw) => {
                raw.complete = false;
            },
            (raw) => {
                raw.complete = false;
                obj(raw.coverage).unprocessed_text_parts = ["word/charts/chart1.xml"];
            },
            (raw) => {
                raw.complete = false;
                obj(raw.coverage).rejected_parts = ["word/embeddings/oleObject1.bin"];
            },
            (raw) => {
                raw.complete = false;
                obj(raw.coverage).unprocessed_text_parts = ["word/charts/chart1.xml"];
                obj(raw.coverage).rejected_parts = ["word/embeddings/oleObject1.bin"];
            },
        ];
        for (const operation of OPERATIONS) {
            for (const change of changes) {
                expectInvalid(text(operation, change), operation);
            }
        }
    });

    it("rejects missing, null and non-boolean complete", () => {
        const values: unknown[] = [undefined, null, "true", "", 0, 1, [], {}];
        for (const operation of OPERATIONS) {
            for (const value of values) {
                expectInvalid(
                    text(operation, (raw) => {
                        if (value === undefined) delete raw.complete;
                        else raw.complete = value;
                    }),
                    operation,
                );
            }
        }
    });

    it("rejects non-empty unprocessed_text_parts with complete true", () => {
        for (const operation of OPERATIONS) {
            expectInvalid(
                text(operation, (raw) => {
                    obj(raw.coverage).unprocessed_text_parts = ["word/charts/chart1.xml"];
                }),
                operation,
            );
        }
    });

    it("rejects non-empty rejected_parts with complete true", () => {
        for (const operation of OPERATIONS) {
            expectInvalid(
                text(operation, (raw) => {
                    obj(raw.coverage).rejected_parts = ["word/embeddings/oleObject1.bin"];
                }),
                operation,
            );
        }
    });

    it("keeps the fixed PII-free detail for completeness contradictions", () => {
        const sensitive = ["Иван Петров", "sekret.docx", "[PERSON_1]"];
        for (const operation of OPERATIONS) {
            const payload = text(operation, (raw) => {
                obj(raw.output).document_name = "sekret.docx";
                obj(raw.statistics).unused_keys = ["[PERSON_1] Иван Петров"];
                obj(raw.coverage).unprocessed_text_parts = ["Иван Петров"];
                obj(raw.coverage).rejected_parts = ["sekret.docx"];
            });
            const message = expectInvalid(payload, operation);
            for (const secret of sensitive) {
                expect(message).not.toContain(secret);
            }
        }
    });
});

describe("parseResultManifest — safe rejection", () => {
    it("reports malformed JSON with the fixed Russian detail", () => {
        try {
            parseResultManifest("{ not json", "mask_file");
            throw new Error("expected rejection");
        } catch (error) {
            expect(error).toBeInstanceOf(ManifestValidationError);
            expect((error as ManifestValidationError).code).toBe("manifest_malformed");
            expect((error as Error).message).toBe("Отчёт о результате повреждён.");
        }
    });

    it("rejects a non-object JSON payload", () => {
        for (const payload of ["null", "[]", '"string"', "42"]) {
            expect(() => parseResultManifest(payload, "mask_file")).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
    });

    it("rejects an unknown schema version", () => {
        for (const version of [0, 2, 99, "1"]) {
            const payload = text("mask_file", (raw) => {
                raw.schema_version = version;
            });
            try {
                parseResultManifest(payload, "mask_file");
                throw new Error("expected rejection");
            } catch (error) {
                expect((error as ManifestValidationError).code).toBe(
                    "manifest_version_unsupported",
                );
                expect((error as Error).message).toBe(
                    "Неподдерживаемая версия отчёта о результате.",
                );
            }
        }
    });

    it("rejects a wrong operation", () => {
        for (const operation of ["unmask_file", "mask_text", 1] as unknown[]) {
            const payload = text("mask_file", (raw) => {
                raw.operation = operation;
            });
            expect(() => parseResultManifest(payload, "mask_file")).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
        expect(() => parseResultManifest(text("unmask_file"), "mask_file")).toThrow(
            "Некорректный отчёт о результате.",
        );
    });

    it("rejects a non-succeeded status", () => {
        for (const status of ["failed", "succeeded ", 1]) {
            const payload = text("mask_file", (raw) => {
                raw.status = status;
            });
            expect(() => parseResultManifest(payload, "mask_file")).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
    });

    it("rejects every missing top-level object", () => {
        for (const field of [
            "complete",
            "masking_ruleset_version",
            "exception_config_digest",
            "input",
            "output",
            "statistics",
            "coverage",
            "cache",
        ]) {
            const payload = text("mask_file", (raw) => {
                delete raw[field];
            });
            expect(() => parseResultManifest(payload, "mask_file"), field).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
    });

    it("rejects missing nested objects and wrong nested primitive types", () => {
        const cases: Array<(raw: Record<string, unknown>) => void> = [
            (raw) => delete obj(raw.input).sha256,
            (raw) => delete obj(raw.input).size_bytes,
            (raw) => delete obj(raw.output).document_name,
            (raw) => delete obj(raw.cache).hit,
            (raw) => delete obj(raw.cache).namespace_version,
            (raw) => {
                obj(raw.input).sha256 = 5;
            },
            (raw) => {
                obj(raw.output).document_name = null;
            },
            (raw) => {
                obj(raw.output).keys_name = 5;
            },
            (raw) => {
                raw.exception_config_digest = 7;
            },
            (raw) => {
                raw.complete = "true";
            },
            (raw) => {
                obj(raw.cache).hit = 1;
            },
        ];
        for (const change of cases) {
            expect(() => parseResultManifest(text("mask_file", change), "mask_file")).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
    });

    it("rejects missing nested arrays/objects in coverage and statistics", () => {
        const cases: Array<[ManifestOperation, (raw: Record<string, unknown>) => void]> = [
            ["mask_file", (raw) => delete obj(raw.statistics).occurrences_by_label],
            ["mask_file", (raw) => delete obj(raw.statistics).unique_by_label],
            ["mask_file", (raw) => delete obj(raw.statistics).total_occurrences],
            ["unmask_file", (raw) => delete obj(raw.statistics).unused_keys],
            ["mask_file", (raw) => delete obj(raw.coverage).processed_parts],
            ["mask_file", (raw) => delete obj(raw.coverage).unprocessed_text_parts],
            ["mask_file", (raw) => delete obj(raw.coverage).rejected_parts],
            ["mask_file", (raw) => delete obj(raw.coverage).raster_image_ocr],
            [
                "mask_file",
                (raw) => {
                    obj(raw.coverage).processed = "not-an-array";
                },
            ],
            [
                "mask_file",
                (raw) => {
                    obj(raw.coverage).processed_parts = "main_document";
                },
            ],
            [
                "mask_file",
                (raw) => {
                    obj(raw.coverage).processed = [{ name: "word/document.xml", category: "main" }];
                },
            ],
            [
                "mask_file",
                (raw) => {
                    delete firstPart(raw).text_nodes;
                },
            ],
        ];
        for (const [operation, change] of cases) {
            expect(
                () => parseResultManifest(text(operation, change), operation),
                String(change),
            ).toThrow("Некорректный отчёт о результате.");
        }
    });

    it("rejects invalid array contents", () => {
        const cases: Array<(raw: Record<string, unknown>) => void> = [
            (raw) => {
                obj(raw.coverage).processed_parts = [1];
            },
            (raw) => {
                obj(raw.coverage).rejected_parts = [null];
            },
            (raw) => {
                obj(obj(raw.statistics).occurrences_by_label).person = "many";
            },
        ];
        for (const change of cases) {
            expect(() => parseResultManifest(text("mask_file", change), "mask_file")).toThrow(
                "Некорректный отчёт о результате.",
            );
        }
        const unmask = text("unmask_file", (raw) => {
            obj(raw.statistics).unused_keys = ["VALUE-B", 5];
        });
        expect(() => parseResultManifest(unmask, "unmask_file")).toThrow(
            "Некорректный отчёт о результате.",
        );
    });

    it("rejects negative, fractional, NaN/Infinity and huge numbers", () => {
        const mutateNumeric = (
            operation: ManifestOperation,
            field: string,
            value: number,
        ): string => {
            const raw = JSON.parse(text(operation)) as Record<string, unknown>;
            if (field === "total_occurrences") {
                obj(raw.statistics).total_occurrences = value;
            } else if (field === "size_bytes") {
                obj(raw.input).size_bytes = value;
            } else if (field === "namespace_version") {
                obj(raw.cache).namespace_version = value;
            } else if (field === "text_nodes") {
                firstPart(raw).text_nodes = value;
            } else if (field === "per_label") {
                obj(obj(raw.statistics).occurrences_by_label).person = value;
            } else {
                raw[field] = value;
            }
            return JSON.stringify(raw);
        };
        const invalidNumbers = [-1, 1.5, Number.MAX_SAFE_INTEGER + 2];
        for (const field of [
            "total_occurrences",
            "size_bytes",
            "namespace_version",
            "text_nodes",
            "per_label",
            "masking_ruleset_version",
        ]) {
            for (const value of invalidNumbers) {
                expect(
                    () =>
                        parseResultManifest(mutateNumeric("mask_file", field, value), "mask_file"),
                    `${field}=${value}`,
                ).toThrow("Некорректный отчёт о результате.");
            }
        }
        // JSON has no NaN literal, but 1e999 parses to Infinity; 0 is a valid count.
        const infinity = text("mask_file").replace(
            '"total_occurrences":2',
            '"total_occurrences":1e999',
        );
        expect(() => parseResultManifest(infinity, "mask_file")).toThrow(
            "Некорректный отчёт о результате.",
        );
        expect(
            parseResultManifest(
                text("mask_file").replace('"size_bytes":10', '"size_bytes":0'),
                "mask_file",
            ).input.size_bytes,
        ).toBe(0);
    });

    it("never echoes input values or PII in the safe error", () => {
        const payload = text("unmask_file", (raw) => {
            obj(raw.statistics).unused_keys = ["Иван Петров"];
            delete obj(raw.output).document_name;
        });
        try {
            parseResultManifest(payload, "unmask_file");
            throw new Error("expected rejection");
        } catch (error) {
            expect(error).toBeInstanceOf(ManifestValidationError);
            expect((error as Error).message).toBe("Некорректный отчёт о результате.");
            expect((error as Error).message).not.toContain("Иван Петров");
        }
    });
});
