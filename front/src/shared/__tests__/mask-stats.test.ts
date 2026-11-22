import { describe, expect, it } from "bun:test";

import { countKeysValues, countLabels, statsBreakdown, totalText } from "@/shared/mask-stats";

describe("countLabels", () => {
    it("counts occurrences and unique values per label", () => {
        const stats = countLabels([
            { label: "person", text: "Иван Петров" },
            { label: "person", text: "Иван Петров" },
            { label: "organization", text: "АО «Росхим»" },
        ]);
        expect(stats.total).toBe(3);
        expect(stats.byLabel).toEqual({ person: 2, organization: 1 });
        expect(stats.uniqueByLabel).toEqual({ person: 1, organization: 1 });
        expect(stats.unused).toEqual([]);
    });

    it("counts links like any other label", () => {
        const stats = countLabels([
            { label: "link", text: "https://example.com" },
            { label: "link", text: "https://example.com" },
        ]);
        expect(stats.total).toBe(2);
        expect(stats.byLabel).toEqual({ link: 2 });
        expect(stats.uniqueByLabel).toEqual({ link: 1 });
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
        const stats = countLabels([
            { label: "person", text: "Иван" },
            { label: "person", text: "Пётр" },
            { label: "person", text: "Иван" },
            { label: "organization", text: "ООО Ромашка" },
        ]);
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
        const stats = countLabels([{ label: "person", text: "Иван" }]);
        expect(totalText(stats)).toBe("1 фрагмент");
        expect(totalText({ ...stats, total: 3 })).toBe("3 фрагмента");
        expect(totalText({ ...stats, total: 5 })).toBe("5 фрагментов");
    });
});
