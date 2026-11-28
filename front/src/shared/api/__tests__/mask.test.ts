import { afterEach, describe, expect, it, mock } from "bun:test";

import { proxyMaskRequest, proxyUnmaskRequest } from "@/shared/api/mask";

import { BACKEND_URL } from "./_fixtures";

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
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
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
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
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

    it("forwards the backend X-Request-ID header", async () => {
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
        globalThis.fetch = mock(
            async () =>
                new Response("{}", {
                    status: 200,
                    headers: { "Content-Type": "application/json", "X-Request-ID": "rid-42" },
                }),
        ) as unknown as typeof fetch;

        const response = await proxyMaskRequest("Иван");
        expect(response.headers.get("X-Request-ID")).toBe("rid-42");
    });

    it("forwards Retry-After on a typed queue_full rejection", async () => {
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
        globalThis.fetch = mock(
            async () =>
                new Response(
                    JSON.stringify({
                        code: "queue_full",
                        detail: "Очередь заданий переполнена. Повторите позже.",
                        request_id: "rid-43",
                    }),
                    {
                        status: 429,
                        headers: {
                            "Content-Type": "application/json",
                            "X-Request-ID": "rid-43",
                            "Retry-After": "45",
                        },
                    },
                ),
        ) as unknown as typeof fetch;

        const response = await proxyMaskRequest("Иван");
        expect(response.status).toBe(429);
        expect(response.headers.get("Retry-After")).toBe("45");
        expect(response.headers.get("X-Request-ID")).toBe("rid-43");
        expect(response.headers.get("Cache-Control")).toBe("no-store");
    });

    it("keeps the backend no-store policy on PII JSON", async () => {
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
        globalThis.fetch = mock(
            async () =>
                new Response("{}", {
                    status: 200,
                    headers: {
                        "Content-Type": "application/json",
                        "Cache-Control": "no-store",
                        Pragma: "no-cache",
                    },
                }),
        ) as unknown as typeof fetch;

        const response = await proxyMaskRequest("Иван");
        expect(response.headers.get("Cache-Control")).toBe("no-store");
        expect(response.headers.get("Pragma")).toBe("no-cache");
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
        process.env.BACKEND_INTERNAL_URL = BACKEND_URL;
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
