import { afterEach, describe, expect, it } from "bun:test";

import JSZip from "jszip";

import { JobFailedError, waitForJob } from "@/shared/api/jobs";

const ORIGINAL_FETCH = globalThis.fetch;

afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
});

const ACCEPTED = {
    job_id: "12345678-1234-4234-8234-123456789abc",
    status: "queued",
    status_url: "/api/jobs/12345678-1234-4234-8234-123456789abc",
    result_url: "/api/jobs/12345678-1234-4234-8234-123456789abc/result",
};

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
