import { describe, expect, it } from "bun:test";

import {
    countKeysValues,
    statsBreakdown,
    statsFromAggregate,
    totalText,
} from "@/shared/mask-stats";

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
    // Keys dedupe by value, so a repeated value is a single key entry.
    const keys = "Физлица:\n  - Иван Петров\n  - Пётр Сидоров\nОрганизации:\n  - АО «Росхим»";

    it("mirrors masking counts and lists keys not found in the document", () => {
        const stats = countKeysValues(keys, "[PERSON_1] и [PERSON_2]");
        expect(stats.total).toBe(3);
        expect(stats.byLabel).toEqual({ person: 2, organization: 1 });
        expect(stats.uniqueByLabel).toEqual({ person: 2, organization: 1 });
        // [ORGANIZATION_1] is absent from the document.
        expect(stats.unused).toEqual(["АО «Росхим»"]);
    });

    it("skips the unused scan when the document text is unknown", () => {
        const stats = countKeysValues(keys, null);
        expect(stats.unused).toEqual([]);
        expect(stats.total).toBe(3);
    });
});

describe("statsBreakdown", () => {
    it("renders one line per label with unique counts", () => {
        const stats = statsFromAggregate({
            total: 4,
            occurrences_by_label: { person: 3, organization: 1 },
            unique_by_label: { person: 2, organization: 1 },
        });
        expect(statsBreakdown(stats)).toBe(
            "Физлица: 3 (2 уникальных)\nОрганизации: 1 (1 уникальный)",
        );
    });

    it("appends a 'Не применено' block when keys were not applied", () => {
        const stats = countKeysValues(
            "Физлица:\n  - Иван\n  - Пётр\nОрганизации:\n  - ООО «Рога и копыта»",
            "[PERSON_1]",
        );
        expect(statsBreakdown(stats)).toBe(
            [
                "Физлица: 2 (2 уникальных)",
                "Организации: 1 (1 уникальный)",
                "",
                "Не применено 2:",
                "- Пётр;",
                "- ООО «Рога и копыта»;",
            ].join("\n"),
        );
    });
});

describe("totalText", () => {
    it("pluralises the total", () => {
        const stats = statsFromAggregate({
            total: 1,
            occurrences_by_label: { person: 1 },
            unique_by_label: { person: 1 },
        });
        expect(totalText(stats)).toBe("1 фрагмент");
        expect(totalText({ ...stats, total: 3 })).toBe("3 фрагмента");
        expect(totalText({ ...stats, total: 5 })).toBe("5 фрагментов");
    });
});
