import { describe, expect, it } from "bun:test";

import { parseKeys, parseSimpleYaml } from "@/shared/masking/keys-parse";

describe("parseKeys — valid documents", () => {
    it("accepts a document with the Профиль marker", () => {
        const result = parseKeys(
            "Профиль: russian-default\nФизлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-2",
        );
        expect(result.valid).toBe(true);
        if (!result.valid) return;
        expect(result.sections).toEqual([
            { title: "Физлица", label: "person", values: ["ЗНАЧЕНИЕ-1", "ЗНАЧЕНИЕ-2"] },
        ]);
        expect(result.entries).toEqual([
            {
                placeholder: "[PERSON_1]",
                label: "person",
                title: "Физлица",
                value: "ЗНАЧЕНИЕ-1",
            },
            {
                placeholder: "[PERSON_2]",
                label: "person",
                title: "Физлица",
                value: "ЗНАЧЕНИЕ-2",
            },
        ]);
    });

    it("accepts a legacy envelope-less document with several sections", () => {
        const result = parseKeys('Физлица:\n  - ЗНАЧЕНИЕ-1\nНомера телефонов:\n  - "+70000000000"');
        expect(result.valid).toBe(true);
        if (!result.valid) return;
        expect(result.entries.map(({ placeholder, value }) => [placeholder, value])).toEqual([
            ["[PERSON_1]", "ЗНАЧЕНИЕ-1"],
            ["[PHONE_1]", "+70000000000"],
        ]);
    });

    it("keeps an empty section valid (backend also accepts an empty list)", () => {
        const result = parseKeys("Физлица:\n");
        expect(result.valid).toBe(true);
        if (!result.valid) return;
        expect(result.sections).toEqual([{ title: "Физлица", label: "person", values: [] }]);
        expect(result.entries).toEqual([]);
    });
});

describe("parseKeys — validation errors", () => {
    it("rejects empty and whitespace-only input", () => {
        expect(parseKeys("")).toMatchObject({ valid: false, error: "Файл ключей пуст." });
        expect(parseKeys("  \n\t")).toMatchObject({ valid: false, error: "Файл ключей пуст." });
    });

    it("rejects malformed YAML lines", () => {
        const result = parseKeys("Физлица:\n  - ЗНАЧЕНИЕ-1\nНЕОЖИДАННАЯ СТРОКА");
        expect(result).toMatchObject({
            valid: false,
            error: "Файл ключей не является корректным YAML.",
        });
    });

    it("rejects a top-level list as a non-mapping", () => {
        expect(parseKeys("- ЗНАЧЕНИЕ-1\n- ЗНАЧЕНИЕ-2")).toMatchObject({
            valid: false,
            error: "Файл ключей должен содержать словарь разделов.",
        });
    });

    it("rejects an unknown section and lists the known titles", () => {
        const result = parseKeys("Телефоны:\n  - +70000000000");
        expect(result.valid).toBe(false);
        if (result.valid) return;
        expect(result.error).toContain("Неизвестный раздел ключей: «Телефоны»");
        expect(result.error).toContain("«Физлица»");
        expect(result.error).toContain("«Адреса»");
    });

    it("rejects version-style metadata as an unknown section", () => {
        expect(parseKeys("версия: 2\nФизлица:\n  - ЗНАЧЕНИЕ-1").valid).toBe(false);
    });

    it("rejects a duplicated section", () => {
        expect(parseKeys("Физлица:\n  - ЗНАЧЕНИЕ-1\nФизлица:\n  - ЗНАЧЕНИЕ-2")).toMatchObject({
            valid: false,
            error: "Раздел «Физлица» встречается в файле больше одного раза.",
        });
    });

    it("rejects duplicate values inside one section", () => {
        expect(parseKeys("Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-1")).toMatchObject({
            valid: false,
            error: "Раздел «Физлица» содержит дубликаты значений.",
        });
    });

    it("rejects empty values", () => {
        expect(parseKeys("Физлица:\n  - ''")).toMatchObject({
            valid: false,
            error: "Раздел «Физлица» должен содержать непустые строки.",
        });
    });

    it("rejects a non-list section", () => {
        expect(parseKeys("Физлица: ЗНАЧЕНИЕ-1")).toMatchObject({
            valid: false,
            error: "Раздел «Физлица» должен быть списком значений.",
        });
    });

    it("rejects a marker without any section (and differs from an empty mapping)", () => {
        expect(parseKeys("Профиль: russian-default")).toMatchObject({
            valid: false,
            error: "Файл ключей содержит метаданные, но не содержит ни одного раздела.",
        });
        expect(parseKeys("# только комментарий")).toMatchObject({
            valid: false,
            error: "Файл ключей должен содержать словарь разделов.",
        });
    });
});

describe("parseKeys — lenient view and purity", () => {
    it("still exposes entries for malformed keys (highlighting is a nicety)", () => {
        const result = parseKeys("Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-1\nНеизвестно:\n  - x");
        expect(result.valid).toBe(false);
        expect(result.entries.map(({ placeholder }) => placeholder)).toEqual([
            "[PERSON_1]",
            "[PERSON_2]",
        ]);
    });

    it("keeps the original array index when an empty value precedes a real one", () => {
        const result = parseKeys("Физлица:\n  - ''\n  - ЗНАЧЕНИЕ-2");
        expect(result.entries.map(({ placeholder }) => placeholder)).toEqual(["[PERSON_2]"]);
    });

    it("returns independent results for repeated calls (no shared mutable state)", () => {
        const text = "Физлица:\n  - ЗНАЧЕНИЕ-1";
        const first = parseKeys(text);
        const second = parseKeys(text);
        expect(second).toEqual(first);
        expect(second.entries).not.toBe(first.entries);
    });
});

describe("parseSimpleYaml (the supported subset)", () => {
    it("parses quoted values and preserves section insertion order", () => {
        expect(
            parseSimpleYaml('Номера телефонов:\n  - "+70000000000"\nФизлица:\n  - ЗНАЧЕНИЕ-1'),
        ).toEqual({
            "Номера телефонов": ["+70000000000"],
            Физлица: ["ЗНАЧЕНИЕ-1"],
        });
    });

    it("throws on an unsupported line", () => {
        expect(() => parseSimpleYaml("Физлица:\n  - ЗНАЧЕНИЕ-1\nНЕОЖИДАННАЯ СТРОКА")).toThrow();
    });
});
