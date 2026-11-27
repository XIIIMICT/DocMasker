import { afterEach, describe, expect, it, jest, spyOn } from "bun:test";

import JSZip from "jszip";

import { cancelJob, JobFailedError, waitForJob } from "@/shared/api/jobs";

const ORIGINAL_FETCH = globalThis.fetch;

afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
});

const ACCEPTED = {
    job_id: "12345678-1234-4234-8234-123456789abc",
    status: "queued",
    status_url: "/api/jobs/12345678-1234-4234-8234-123456789abc",
    result_url: "/api/jobs/12345678-1234-4234-8234-123456789abc/result",
} as const;

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

async function zipResponse(entries: Record<string, string>): Promise<Response> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(entries)) zip.file(name, content);
    const blob = await zip.generateAsync({ type: "blob" });
    return new Response(blob, { status: 200, headers: { "Content-Type": "application/zip" } });
}

describe("cancelJob", () => {
    it("reports an accepted server cancellation only on 2xx", async () => {
        globalThis.fetch = (async () =>
            new Response(null, { status: 204 })) as unknown as typeof fetch;
        expect(await cancelJob(ACCEPTED)).toEqual({ kind: "accepted" });
    });

    it("does not claim cancellation on a server error", async () => {
        globalThis.fetch = (async () =>
            new Response(null, { status: 500 })) as unknown as typeof fetch;
        expect(await cancelJob(ACCEPTED)).toEqual({ kind: "failed", reason: "server" });
    });

    it("does not claim cancellation on a network failure", async () => {
        globalThis.fetch = (async () => {
            throw new TypeError("network down");
        }) as unknown as typeof fetch;
        expect(await cancelJob(ACCEPTED)).toEqual({ kind: "failed", reason: "network" });
    });

    it("reports a job that finished just before the DELETE", async () => {
        globalThis.fetch = (async () =>
            new Response(null, { status: 404 })) as unknown as typeof fetch;
        expect(await cancelJob(ACCEPTED)).toEqual({ kind: "already_terminal" });
    });
});

describe("waitForJob", () => {
    it("polls through queued/running and unpacks the result ZIP", async () => {
        const calls: string[] = [];
        let polls = 0;
        globalThis.fetch = (async (url: string | URL | Request) => {
            const path = String(url);
            calls.push(path);
            if (path === ACCEPTED.status_url) {
                polls += 1;
                return jsonResponse({
                    job_id: ACCEPTED.job_id,
                    status: polls >= 3 ? "succeeded" : polls === 1 ? "queued" : "running",
                    phase: polls >= 3 ? "completed" : "detection",
                    progress: polls >= 3 ? 100 : 40,
                    attempt: 1,
                    error_code: null,
                    result_url: polls >= 3 ? ACCEPTED.result_url : null,
                });
            }
            return zipResponse({
                "result.json": JSON.stringify({
                    text: "[PERSON_1]",
                    keys: "Физлица:\n  - Иван Петров",
                }),
            });
        }) as unknown as typeof fetch;

        const result = await waitForJob(ACCEPTED);
        expect(calls.filter((c) => c === ACCEPTED.status_url).length).toBe(3);
        expect(calls).toContain(ACCEPTED.result_url);
        const payload = await result.json<{ text: string; keys: string }>();
        expect(payload.text).toBe("[PERSON_1]");
        expect(payload.keys).toContain("Иван Петров");
        expect(result.entry(".json")).not.toBeNull();
    });

    it("throws a safe error when the job fails", async () => {
        globalThis.fetch = (async () =>
            jsonResponse({
                job_id: ACCEPTED.job_id,
                status: "failed",
                phase: "detection",
                progress: 0,
                attempt: 1,
                error_code: "internal_error",
                result_url: null,
            })) as unknown as typeof fetch;
        await expect(waitForJob(ACCEPTED)).rejects.toBeInstanceOf(JobFailedError);
    });

    it("stops on abort", async () => {
        const controller = new AbortController();
        globalThis.fetch = (async () => {
            controller.abort();
            return jsonResponse({
                job_id: ACCEPTED.job_id,
                status: "running",
                phase: "detection",
                progress: 10,
                attempt: 1,
                error_code: null,
                result_url: null,
            });
        }) as unknown as typeof fetch;
        await expect(waitForJob(ACCEPTED, controller.signal)).rejects.toBeInstanceOf(
            JobFailedError,
        );
    });

    it("rejects a cancellation confirmed while the result is being read", async () => {
        const controller = new AbortController();
        globalThis.fetch = (async (url: string | URL | Request) => {
            if (String(url) === ACCEPTED.status_url) {
                return jsonResponse({
                    job_id: ACCEPTED.job_id,
                    status: "succeeded",
                    phase: "completed",
                    progress: 100,
                    attempt: 1,
                    error_code: null,
                    result_url: ACCEPTED.result_url,
                });
            }
            const response = await zipResponse({
                "result.json": JSON.stringify({ text: "VALUE-A" }),
            });
            // The job won the race, but cancellation was confirmed before the
            // result was unpacked: no result may escape to the UI.
            controller.abort();
            return response;
        }) as unknown as typeof fetch;

        await expect(waitForJob(ACCEPTED, controller.signal)).rejects.toThrow("Задание отменено.");
    });

    it("stops unpacking when the signal aborts during the archive read", async () => {
        const controller = new AbortController();
        const originalArrayBuffer = Blob.prototype.arrayBuffer;
        let reads = 0;
        const readSpy = spyOn(Blob.prototype, "arrayBuffer").mockImplementation(async function (
            this: Blob,
        ) {
            const buffer = await originalArrayBuffer.call(this);
            reads += 1;
            if (reads === 1) controller.abort();
            return buffer;
        });
        globalThis.fetch = (async (url: string | URL | Request) => {
            if (String(url) === ACCEPTED.status_url) {
                return jsonResponse({
                    job_id: ACCEPTED.job_id,
                    status: "succeeded",
                    phase: "completed",
                    progress: 100,
                    attempt: 1,
                    error_code: null,
                    result_url: ACCEPTED.result_url,
                });
            }
            return zipResponse({ "result.json": JSON.stringify({ text: "VALUE-A" }) });
        }) as unknown as typeof fetch;
        try {
            await expect(waitForJob(ACCEPTED, controller.signal)).rejects.toBeInstanceOf(
                JobFailedError,
            );
            expect(reads).toBeGreaterThan(0);
        } finally {
            readSpy.mockRestore();
        }
    });

    it("removes one abort listener for every poll delay", async () => {
        const addSpy = spyOn(AbortSignal.prototype, "addEventListener");
        const removeSpy = spyOn(AbortSignal.prototype, "removeEventListener");
        let polls = 0;
        globalThis.fetch = (async (url: string | URL | Request) => {
            if (String(url) === ACCEPTED.result_url) {
                return zipResponse({ "result.json": "{}" });
            }
            polls += 1;
            return jsonResponse({
                job_id: ACCEPTED.job_id,
                status: polls >= 2 ? "succeeded" : "running",
                phase: polls >= 2 ? "completed" : "detection",
                progress: polls >= 2 ? 100 : 40,
                attempt: 1,
                error_code: null,
                result_url: polls >= 2 ? ACCEPTED.result_url : null,
            });
        }) as unknown as typeof fetch;
        try {
            await waitForJob(ACCEPTED, new AbortController().signal);
            const added = addSpy.mock.calls.filter((call) => call[0] === "abort").length;
            const removed = removeSpy.mock.calls.filter((call) => call[0] === "abort").length;
            expect(added).toBe(1);
            expect(removed).toBe(1);
        } finally {
            addSpy.mockRestore();
            removeSpy.mockRestore();
        }
    });

    it("aborts cleanly during a polling delay", async () => {
        jest.useFakeTimers();
        const addSpy = spyOn(AbortSignal.prototype, "addEventListener");
        try {
            const controller = new AbortController();
            globalThis.fetch = (async () =>
                jsonResponse({
                    job_id: ACCEPTED.job_id,
                    status: "running",
                    phase: "detection",
                    progress: 10,
                    attempt: 1,
                    error_code: null,
                    result_url: null,
                })) as unknown as typeof fetch;
            const pending = waitForJob(ACCEPTED, controller.signal);
            // A real 10 ms sleep used to race the first poll: if the signal
            // aborted before the backoff listener was armed, the test hung.
            // Fake timers plus the listener handshake fix the ordering.
            let spins = 0;
            while (!addSpy.mock.calls.some((call) => call[0] === "abort")) {
                if (++spins > 100) throw new Error("the poll delay never armed");
                await Promise.resolve();
            }
            setTimeout(() => controller.abort(), 10);
            jest.advanceTimersByTime(10);
            await expect(pending).rejects.toBeInstanceOf(JobFailedError);
        } finally {
            addSpy.mockRestore();
            jest.useRealTimers();
        }
    });

    it("does not log or expose PII in errors", async () => {
        globalThis.fetch = (async () =>
            jsonResponse({ detail: "Иван Петров" }, 500)) as unknown as typeof fetch;
        try {
            await waitForJob(ACCEPTED);
            expect.unreachable();
        } catch (error) {
            expect(error).toBeInstanceOf(JobFailedError);
            expect(String(error)).not.toContain("Иван");
        }
    });
});
