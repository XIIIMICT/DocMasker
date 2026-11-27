import { describe, expect, it } from "bun:test";

import { isValidElement, type ReactNode } from "react";

import type { ResultManifest } from "@/shared/api/mask-archive";
import {
    countKeysValues,
    statsBreakdown,
    statsFromAggregate,
    statsFromManifest,
    statsFromParsedKeys,
    totalText,
    uniqueTotal,
} from "@/shared/mask-stats";
import { parseKeys } from "@/shared/masking/keys-parse";

function textOf(nodes: ReactNode[]): string {
    return nodes
        .map((node) => {
            if (typeof node === "string") return node;
            if (isValidElement(node)) {
                const children = (node.props as { children?: ReactNode }).children;
                return Array.isArray(children) ? textOf(children) : textOf([children as ReactNode]);
            }
            return "";
        })
        .join("");
}

describe("statsFromAggregate", () => {
    it("maps the backend aggregates to the display stats", () => {
        const stats = statsFromAggregate({
            total: 3,
            occurrences_by_label: { person: 2, organization: 1 },
            unique_by_label: { person: 1, organization: 1 },
        });
        expect(stats.total).toBe(3);
        expect(stats.byLabel).toEqual({ person: 2, organization: 1 });
        expect(stats.uniqueByLabel).toEqual({ person: 1, organization: 1 });
        expect(stats.unused).toEqual([]);
    });

    it("passes through an empty aggregate", () => {
        const stats = statsFromAggregate({
            total: 0,
            occurrences_by_label: {},
            unique_by_label: {},
        });
        expect(stats.total).toBe(0);
        expect(stats.byLabel).toEqual({});
        expect(stats.uniqueByLabel).toEqual({});
    });
});

describe("countKeysValues", () => {
    const keys = "Физлица:\n  - Иван Петров\n  - Пётр Сидоров\nОрганизации:\n  - АО «Росхим»";

    it("counts placeholder occurrences and lists keys not found in the document", () => {
        const stats = countKeysValues(keys, "[PERSON_1] и [PERSON_2]");
        expect(stats.total).toBe(2);
        expect(stats.byLabel).toEqual({ person: 2 });
        expect(stats.uniqueByLabel).toEqual({ person: 2, organization: 1 });
        expect(stats.unused).toEqual(["АО «Росхим»"]);
    });

    it("uses externally counted occurrences for a DOCX with no readable text", () => {
        const stats = countKeysValues(keys, null, { person: 5, organization: 3 });
        expect(stats.byLabel).toEqual({ person: 5, organization: 3 });
        expect(stats.uniqueByLabel).toEqual({ person: 2, organization: 1 });
        expect(stats.unused).toEqual([]);
    });

    it("counts repeated placeholders and lists keys missing from the document", () => {
        const keys = "Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-2\n  - ЗНАЧЕНИЕ-3";
        const stats = countKeysValues(
            keys,
            "[PERSON_1] [PERSON_1] [PERSON_1] [PERSON_2] [PERSON_2]",
        );
        expect(stats.byLabel).toEqual({ person: 5 });
        expect(stats.uniqueByLabel).toEqual({ person: 3 });
        expect(stats.unused).toEqual(["ЗНАЧЕНИЕ-3"]);
        expect(stats.total).toBe(5);
    });

    it("ignores placeholder-shaped tokens that are not in the keys", () => {
        const stats = countKeysValues("Физлица:\n  - ЗНАЧЕНИЕ-1", "[PERSON_1] [NEIZVESTNO_1]");
        expect(stats.byLabel).toEqual({ person: 1 });
        expect(stats.total).toBe(1);
        expect(stats.unused).toEqual([]);
    });

    it("keeps distinct counts separate from occurrences across labels", () => {
        const keys =
            "Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-2\nНомера телефонов:\n  - +70000000000\nОрганизации:\n  - ООО «Тест»";
        const stats = countKeysValues(
            keys,
            "[PERSON_1] [PERSON_1] [PERSON_2] [PERSON_2] [PHONE_1] [PHONE_1]",
        );
        expect(stats.byLabel).toEqual({ person: 4, phone: 2 });
        expect(stats.uniqueByLabel).toEqual({ person: 2, phone: 1, organization: 1 });
        expect(stats.unused).toEqual(["ООО «Тест»"]);
    });

    it("keeps duplicate values as two entries with their own placeholders", () => {
        const stats = countKeysValues("Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-1", "[PERSON_1]");
        expect(stats.byLabel).toEqual({ person: 1 });
        expect(stats.uniqueByLabel).toEqual({ person: 1 });
        expect(stats.unused).toEqual(["ЗНАЧЕНИЕ-1"]);
    });

    it("returns empty stats for empty keys", () => {
        const stats = countKeysValues("", "[PERSON_1]");
        expect(stats).toEqual({ total: 0, byLabel: {}, uniqueByLabel: {}, unused: [] });
    });

    it("accepts one parsed document for both validation and statistics", () => {
        const keys = "Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-2";
        const text = "[PERSON_1] [PERSON_1] [PERSON_2]";
        const parsed = parseKeys(keys);
        expect(parsed.valid).toBe(true);
        expect(statsFromParsedKeys(parsed, text)).toEqual(countKeysValues(keys, text));
    });

    it("counts a large synthetic document with one scan", () => {
        const keyCount = 2000;
        const keys =
            "Физлица:\n" +
            Array.from({ length: keyCount }, (_, index) => `  - ЗНАЧЕНИЕ-${index + 1}`).join("\n");
        const document = Array.from({ length: keyCount }, (_, index) =>
            `[PERSON_${index + 1}]`.repeat(10),
        ).join(" ");
        const stats = countKeysValues(keys, document);
        expect(stats.byLabel.person).toBe(keyCount * 10);
        expect(stats.uniqueByLabel.person).toBe(keyCount);
        expect(stats.unused).toEqual([]);
        expect(stats.total).toBe(keyCount * 10);
    });
});

describe("statsBreakdown", () => {
    it("shows unique values first and total occurrences in parentheses", () => {
        const stats = statsFromAggregate({
            total: 4,
            occurrences_by_label: { person: 3, organization: 1 },
            unique_by_label: { person: 2, organization: 1 },
        });
        expect(textOf(statsBreakdown(stats))).toBe(
            "Физлица: 2 (3 с повторами)\nОрганизации: 1 (1 с повторами)",
        );
    });

    it("renders category names in bold", () => {
        const stats = statsFromAggregate({
            total: 1,
            occurrences_by_label: { organization: 1 },
            unique_by_label: { organization: 1 },
        });
        const nodes = statsBreakdown(stats);
        const bold = nodes.find((node) => isValidElement(node));
        expect(isValidElement(bold)).toBe(true);
        if (isValidElement(bold)) {
            expect(bold.type).toBe("b");
            expect((bold.props as { children?: ReactNode }).children).toBe("Организации:");
        }
    });

    it("appends a 'Не применено' block when keys were not applied", () => {
        const stats = countKeysValues(
            "Физлица:\n  - Иван\n  - Пётр\nОрганизации:\n  - ООО «Рога и копыта»",
            "[PERSON_1]",
        );
        expect(textOf(statsBreakdown(stats))).toBe(
            [
                "Физлица: 2 (1 с повторами)",
                "",
                "Не применено 2:",
                "- Пётр;",
                "- ООО «Рога и копыта»;",
            ].join("\n"),
        );
    });
});

describe("totalText", () => {
    it("counts distinct values, not raw occurrences", () => {
        const stats = statsFromAggregate({
            total: 7,
            occurrences_by_label: { person: 5, organization: 2 },
            unique_by_label: { person: 1, organization: 1 },
        });
        expect(uniqueTotal(stats)).toBe(2);
        expect(totalText(stats)).toBe("2 фрагмента");
        expect(
            totalText(
                statsFromAggregate({
                    total: 1,
                    occurrences_by_label: { person: 1 },
                    unique_by_label: { person: 1 },
                }),
            ),
        ).toBe("1 фрагмент");
        expect(
            totalText(
                statsFromAggregate({
                    total: 9,
                    occurrences_by_label: { person: 9 },
                    unique_by_label: { person: 5 },
                }),
            ),
        ).toBe("5 фрагментов");
    });
});

describe("statsFromManifest", () => {
    it("fails closed on partial statistics instead of showing zero fragments", () => {
        const partial = { statistics: { unused_keys: [] } } as unknown as ResultManifest;
        expect(() => statsFromManifest(partial)).toThrow("Некорректный отчёт о результате.");
    });
});

describe("file mask/demask stats parity", () => {
    it("shows the same unique counts when the keys are not changed", () => {
        const manifest = {
            statistics: {
                total_occurrences: 5,
                occurrences_by_label: { person: 3, organization: 2 },
                unique_by_label: { person: 2, organization: 1 },
            },
            coverage: {},
        } as unknown as ResultManifest;
        const masked = statsFromManifest(manifest);
        const demasked = countKeysValues(
            "Физлица:\n  - Иван Петров\n  - Пётр Сидоров\nОрганизации:\n  - АО «Росхим»",
            null,
            masked.byLabel,
        );
        expect(demasked.uniqueByLabel).toEqual(masked.uniqueByLabel);
        expect(uniqueTotal(demasked)).toBe(uniqueTotal(masked));
        expect(demasked.byLabel).toEqual(masked.byLabel);
        expect(textOf(statsBreakdown(demasked))).toBe(textOf(statsBreakdown(masked)));
    });
});
