import { afterEach, describe, expect, it, mock } from "bun:test";

import {
    parseSimpleYaml,
    placeholdersFromKeys,
    proxyMaskRequest,
    proxyUnmaskRequest,
    validateKeysYaml,
    valuesFromKeys,
} from "@/shared/api/mask";

const ORIGINAL_FETCH = globalThis.fetch;
const ORIGINAL_URL = process.env.BACKEND_INTERNAL_URL;

afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    if (ORIGINAL_URL === undefined) {
        delete process.env.BACKEND_INTERNAL_URL;
    } else {
        process.env.BACKEND_INTERNAL_URL = ORIGINAL_URL;
    }
});

describe("proxyMaskRequest", () => {
    it("forwards the text to the backend and passes the response through", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        const fetchMock = mock(async (url: URL | RequestInfo, init?: RequestInit) => {
            expect(String(url)).toBe("http://back:8001/api/mask");
            expect(init?.method).toBe("POST");
            expect(JSON.parse(String(init?.body))).toEqual({ text: "Иван" });
            return new Response(
                JSON.stringify({
                    text: "[PERSON_1]",
                    stats: { total: 0, occurrences_by_label: {}, unique_by_label: {} },
                    keys: "",
                }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            );
        });
        globalThis.fetch = fetchMock as unknown as typeof fetch;

        const response = await proxyMaskRequest("Иван");
        expect(response.status).toBe(200);
        expect(await response.json()).toEqual({
            text: "[PERSON_1]",
            stats: { total: 0, occurrences_by_label: {}, unique_by_label: {} },
            keys: "",
        });
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("propagates a non-2xx status verbatim", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        globalThis.fetch = mock(
            async () =>
                new Response(JSON.stringify({ detail: "Текст пуст." }), {
                    status: 422,
                    headers: { "Content-Type": "application/json" },
                }),
        ) as unknown as typeof fetch;

        const response = await proxyMaskRequest("");
        expect(response.status).toBe(422);
        expect(await response.json()).toEqual({ detail: "Текст пуст." });
    });
});

describe("proxy timeouts", () => {
    it("exposes bounded timeouts (no global 0/unbounded dispatcher)", async () => {
        const { PROXY_FILE_TIMEOUT_MS, PROXY_JSON_TIMEOUT_MS } = await import("@/shared/api/mask");
        expect(PROXY_JSON_TIMEOUT_MS).toBeGreaterThan(0);
        expect(PROXY_FILE_TIMEOUT_MS).toBeGreaterThan(PROXY_JSON_TIMEOUT_MS);
        expect(PROXY_FILE_TIMEOUT_MS).toBeLessThanOrEqual(600_000);
    });
});

describe("proxyUnmaskRequest", () => {
    it("sends the masked text and keys, passing the response through", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        const fetchMock = mock(async (url: URL | RequestInfo, init?: RequestInit) => {
            expect(String(url)).toBe("http://back:8001/api/unmask");
            expect(init?.method).toBe("POST");
            expect(JSON.parse(String(init?.body))).toEqual({
                text: "[PERSON_1]",
                keys: "Физлица:",
            });
            return new Response(JSON.stringify({ text: "Иван" }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            });
        });
        globalThis.fetch = fetchMock as unknown as typeof fetch;

        const response = await proxyUnmaskRequest("[PERSON_1]", "Физлица:");
        expect(response.status).toBe(200);
        expect(await response.json()).toEqual({ text: "Иван" });
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });
});

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
