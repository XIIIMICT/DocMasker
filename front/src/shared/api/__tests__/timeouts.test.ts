import { afterEach, describe, expect, it, mock } from "bun:test";

import { passThrough, postToBackend } from "@/shared/api/proxy";
import {
    JOB_TIMEOUT_MS,
    JOB_WAIT_TIMEOUT_MS,
    JSON_TIMEOUT_MS,
    RESULT_TIMEOUT_MS,
    UPLOAD_TIMEOUT_MS,
    timeoutForRoute,
    type ProxyRoute,
} from "@/shared/api/timeouts";

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

describe("canonical timeout policy (§8.4)", () => {
    it("every value is finite and positive (no unbounded 0)", () => {
        for (const value of [
            JSON_TIMEOUT_MS,
            JOB_TIMEOUT_MS,
            UPLOAD_TIMEOUT_MS,
            RESULT_TIMEOUT_MS,
            JOB_WAIT_TIMEOUT_MS,
        ]) {
            expect(Number.isFinite(value)).toBe(true);
            expect(value).toBeGreaterThan(0);
        }
    });

    it("short JSON and job calls are fast, uploads and result downloads are longer but bounded", () => {
        expect(JSON_TIMEOUT_MS).toBe(15_000);
        expect(JOB_TIMEOUT_MS).toBe(JSON_TIMEOUT_MS);
        expect(UPLOAD_TIMEOUT_MS).toBeGreaterThan(JSON_TIMEOUT_MS);
        expect(RESULT_TIMEOUT_MS).toBeGreaterThan(UPLOAD_TIMEOUT_MS);
        expect(RESULT_TIMEOUT_MS).toBeLessThanOrEqual(600_000);
        // Job-wait is a client workflow deadline, not a proxy request timeout.
        expect(JOB_WAIT_TIMEOUT_MS).toBeGreaterThan(RESULT_TIMEOUT_MS);
    });

    it("maps every route to a documented, finite timeout", () => {
        const routes: ProxyRoute[] = [
            "mask",
            "unmask",
            "labels",
            "mask-file",
            "unmask-file",
            "job-submit",
            "job-status",
            "job-cancel",
            "job-result",
        ];
        const expected = [
            JSON_TIMEOUT_MS,
            JSON_TIMEOUT_MS,
            JSON_TIMEOUT_MS,
            UPLOAD_TIMEOUT_MS,
            UPLOAD_TIMEOUT_MS,
            JOB_TIMEOUT_MS,
            JOB_TIMEOUT_MS,
            JOB_TIMEOUT_MS,
            RESULT_TIMEOUT_MS,
        ];
        expect(routes.map(timeoutForRoute)).toEqual(expected);
    });

    it("never maps a short JSON route to the long file timeout", () => {
        // The original bug: proxyMaskRequest/proxyUnmaskRequest used 600 s.
        expect(timeoutForRoute("mask")).toBeLessThan(UPLOAD_TIMEOUT_MS);
        expect(timeoutForRoute("unmask")).toBeLessThan(RESULT_TIMEOUT_MS);
    });
});

describe("postToBackend", () => {
    it("uses the canonical route timeout and no-store caching", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        const fetchMock = mock(async (url: URL | RequestInfo, init?: RequestInit) => {
            expect(String(url)).toBe("http://back:8001/api/mask");
            expect(init?.cache).toBe("no-store");
            expect(init?.signal).toBeDefined();
            return new Response("{}", { status: 200 });
        });
        globalThis.fetch = fetchMock as unknown as typeof fetch;

        await postToBackend("/api/mask", { method: "POST" }, "mask");
        expect(fetchMock).toHaveBeenCalledTimes(1);
    });

    it("combines the caller's abort signal with the route timeout", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        const controller = new AbortController();
        let seen: AbortSignal | undefined;
        globalThis.fetch = mock(async (_url: URL | RequestInfo, init?: RequestInit) => {
            seen = init?.signal ?? undefined;
            return new Response("{}", { status: 200 });
        }) as unknown as typeof fetch;

        await postToBackend("/api/mask-file", { method: "POST" }, "mask-file", controller.signal);
        expect(seen).toBeDefined();
        expect(seen?.aborted).toBe(false);
        controller.abort();
        expect(seen?.aborted).toBe(true); // AbortSignal.any propagates the caller's abort
    });

    it("forwards a streamed request body unchanged (no arrayBuffer copy)", async () => {
        process.env.BACKEND_INTERNAL_URL = "http://back:8001";
        const stream = new ReadableStream<Uint8Array>({
            start(controller) {
                controller.enqueue(new TextEncoder().encode("multipart-bytes"));
                controller.close();
            },
        });
        let seenBody: BodyInit | null | undefined;
        globalThis.fetch = mock(async (_url: URL | RequestInfo, init?: RequestInit) => {
            seenBody = init?.body;
            return new Response("{}", { status: 200 });
        }) as unknown as typeof fetch;

        await postToBackend("/api/mask-file", { method: "POST", body: stream }, "mask-file");
        expect(seenBody).toBe(stream); // identity, not a rebuilt copy
    });
});

describe("passThrough", () => {
    it("streams the body and forwards error/metadata headers", async () => {
        const upstream = new Response("zip-bytes", {
            status: 200,
            headers: {
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=x.zip",
                "Cache-Control": "no-store",
                Pragma: "no-cache",
                "X-Request-ID": "rid-123",
            },
        });
        const result = passThrough(upstream, ["Content-Disposition", "Cache-Control"]);
        expect(result.status).toBe(200);
        expect(result.headers.get("Content-Type")).toBe("application/zip");
        expect(result.headers.get("Content-Disposition")).toBe("attachment; filename=x.zip");
        expect(result.headers.get("Cache-Control")).toBe("no-store");
        expect(result.headers.get("Pragma")).toBe("no-cache");
        expect(result.headers.get("X-Request-ID")).toBe("rid-123");
        expect(await result.text()).toBe("zip-bytes");
    });

    it("defaults to no-store when the upstream omits cache headers", async () => {
        const result = passThrough(new Response("{}", { status: 200 }));
        expect(result.headers.get("Cache-Control")).toBe("no-store");
        expect(result.headers.get("Pragma")).toBe("no-cache");
    });
});
