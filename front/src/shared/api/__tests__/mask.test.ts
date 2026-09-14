import { afterEach, describe, expect, it, mock } from "bun:test";

import { proxyMaskRequest } from "@/shared/api/mask";

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
            return new Response(JSON.stringify({ text: "[PERSON]", masked_entities: [] }), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            });
        });
        globalThis.fetch = fetchMock as unknown as typeof fetch;

        const response = await proxyMaskRequest("Иван");
        expect(response.status).toBe(200);
        expect(await response.json()).toEqual({ text: "[PERSON]", masked_entities: [] });
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
