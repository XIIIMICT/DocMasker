import { describe, expect, it } from "bun:test";

import {
    parseSimpleYaml,
    placeholdersFromKeys,
    validateKeysYaml,
    valuesFromKeys,
} from "@/shared/api/mask";
import { parseKeys } from "@/shared/masking/keys-parse";

describe("validateKeysYaml", () => {
    it("accepts a well-formed document", () => {
        expect(
            validateKeysYaml("Физлица:\n  - Иван Петров\nОрганизации:\n  - АО «Росхим»"),
        ).toBeNull();
    });

    it("accepts a file with the Профиль marker", () => {
        const keys = "Профиль: russian-default\nФизлица:\n  - Иван Петров";
        expect(validateKeysYaml(keys)).toBeNull();
    });

    it("accepts a legacy envelope-less file (default profile)", () => {
        expect(validateKeysYaml("Физлица:\n  - Иван Петров")).toBeNull();
    });

    it("rejects version keys (never shipped, not part of the format)", () => {
        const old =
            "версия: 2\nпрофиль: russian-default\nверсия_профиля: 1\nФизлица:\n  - Иван Петров";
        expect(validateKeysYaml(old)).toMatch(/Неизвестный раздел/);
    });

    it("rejects a file with the marker but without sections", () => {
        expect(validateKeysYaml("Профиль: russian-default")).toContain(
            "не содержит ни одного раздела",
        );
    });

    it("rejects empty input", () => {
        expect(validateKeysYaml("   ")).toMatch(/пуст/);
    });

    it("rejects a document with an unknown section", () => {
        expect(validateKeysYaml("Телефоны:\n  - +7 900")).toMatch(/Неизвестный раздел/);
    });

    it("rejects a non-mapping", () => {
        expect(validateKeysYaml("- просто\n- список")).toMatch(/словарь разделов/);
    });

    it("rejects a non-list section", () => {
        expect(validateKeysYaml("Физлица: Иван")).toMatch(/должен быть списком/);
    });

    it("rejects an empty item", () => {
        expect(validateKeysYaml("Физлица:\n  - ''")).toMatch(/непустые строки/);
    });

    it("rejects duplicate values within a section", () => {
        expect(validateKeysYaml("Физлица:\n  - Иван Петров\n  - Иван Петров")).toMatch(
            /дубликаты значений/,
        );
    });

    it("rejects a duplicated section", () => {
        expect(validateKeysYaml("Физлица:\n  - Иван\nФизлица:\n  - Пётр")).toMatch(
            /больше одного раза/,
        );
    });

    it("runs on every change: valid then invalid", () => {
        const good = "Физлица:\n  - Иван";
        const bad = "Физлица:\n  - Иван\nНеизвестно:\n  - x";
        expect(validateKeysYaml(good)).toBeNull();
        expect(validateKeysYaml(bad)).toMatch(/Неизвестный раздел/);
    });
});

describe("parseSimpleYaml", () => {
    it("parses sections with quoted values", () => {
        expect(parseSimpleYaml('Номера телефонов:\n  - "+79494271795"')).toEqual({
            "Номера телефонов": ["+79494271795"],
        });
    });

    it("parses two sections preserving order of insertion", () => {
        expect(parseSimpleYaml("Физлица:\n  - Иван\nОрганизации:\n  - АО Ромашка")).toEqual({
            Физлица: ["Иван"],
            Организации: ["АО Ромашка"],
        });
    });
});

describe("shared parse result", () => {
    it("lets validation and extraction reuse one parsed document", () => {
        const keys = "Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-2";
        const parsed = parseKeys(keys);
        expect(validateKeysYaml(parsed)).toBeNull();
        expect(placeholdersFromKeys(parsed)).toEqual(["[PERSON_1]", "[PERSON_2]"]);
        expect(valuesFromKeys(parsed)).toEqual(["ЗНАЧЕНИЕ-1", "ЗНАЧЕНИЕ-2"]);
    });

    it("reports the same error from a parsed result and from raw YAML", () => {
        const bad = "Физлица:\n  - ЗНАЧЕНИЕ-1\n  - ЗНАЧЕНИЕ-1";
        expect(validateKeysYaml(parseKeys(bad))).toBe(validateKeysYaml(bad));
    });
});

describe("placeholdersFromKeys / valuesFromKeys", () => {
    const keys =
        'Физлица:\n  - Иван Петров\n  - Пётр Сидоров\nНомера телефонов:\n  - "+79494271795"';

    it("numbers placeholders per section in document order", () => {
        expect(placeholdersFromKeys(keys)).toEqual(["[PERSON_1]", "[PERSON_2]", "[PHONE_1]"]);
    });

    it("returns the original values in the same order", () => {
        expect(valuesFromKeys(keys)).toEqual(["Иван Петров", "Пётр Сидоров", "+79494271795"]);
    });

    it("returns empty lists for unparseable input rather than throwing", () => {
        expect(placeholdersFromKeys("- не словарь")).toEqual([]);
        expect(valuesFromKeys("")).toEqual([]);
    });
});
