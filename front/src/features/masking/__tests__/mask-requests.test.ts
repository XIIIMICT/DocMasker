import { afterEach, describe, expect, it } from "bun:test";

import JSZip from "jszip";

import { JobFailedError, type JobAccepted } from "@/shared/api/jobs";

import {
    operationError,
    postMaskFile,
    postMaskText,
    postUnmaskFile,
    postUnmaskText,
    readErrorDetail,
    resolveMaskBundle,
    resolveMaskResponse,
    resolveRestoreBundle,
    resolveUnmaskResponse,
    type JobSession,
} from "../mask-requests";

const ORIGINAL_FETCH = globalThis.fetch;
const JOB_ID = "12345678-1234-4234-8234-123456789abc";

const ACCEPTED: JobAccepted = {
    job_id: JOB_ID,
    status: "queued",
    status_url: `/api/jobs/${JOB_ID}`,
    result_url: `/api/jobs/${JOB_ID}/result`,
};

const AGGREGATE = {
    total: 1,
    occurrences_by_label: { person: 1 },
    unique_by_label: { person: 1 },
};

afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
});

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

async function zipResponse(entries: Record<string, string>): Promise<Response> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(entries)) zip.file(name, content);
    return new Response(await zip.generateAsync({ type: "blob" }), {
        status: 200,
        headers: { "Content-Type": "application/zip" },
    });
}

function manifest(operation: "mask_file" | "unmask_file"): string {
    return JSON.stringify({
        schema_version: 1,
        operation,
        status: "succeeded",
        complete: true,
        masking_ruleset_version: 1,
        exception_config_digest: "digest",
        input: { sha256: "abc", size_bytes: 10 },
        output: { document_name: "doc.docx", keys_name: "doc.yaml" },
        statistics: {
            total_occurrences: 1,
            occurrences_by_label: { person: 1 },
            unique_by_label: { person: 1 },
            unused_keys: ["VALUE-B"],
        },
        coverage: {
            processed_parts: ["main_document"],
            unprocessed_text_parts: [],
            rejected_parts: [],
            raster_image_ocr: "not_performed",
        },
        cache: { hit: false, namespace_version: 1 },
    });
}

const MASK_ZIP = {
    "doc (замаскированный).docx": "docx-bytes",
    "doc (ключи демаскировки).yaml": "Физлица:\n  - VALUE-A",
    "manifest.json": manifest("mask_file"),
};
const RESTORE_ZIP = {
    "doc (демаскированный).docx": "restored-bytes",
    "manifest.json": manifest("unmask_file"),
};

function fakeJobs() {
    const calls: string[] = [];
    const pollingController: { current: AbortController | null } = { current: null };
    const session: JobSession = {
        async trackAccepted(response: Response) {
            calls.push("track");
            await response.json();
            return ACCEPTED;
        },
        endJob() {
            calls.push("end");
        },
        pollingController,
    };
    return { calls, pollingController, session };
}

/** Mock fetch: one sync/accepted operation response plus a succeeded job. */
function mockTerminalJob(operationResponse: Response, resultEntries: Record<string, string>) {
    const requests: string[] = [];
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        const path = String(url);
        requests.push(path);
        if (path === ACCEPTED.status_url) {
            return jsonResponse({
                job_id: JOB_ID,
                status: "succeeded",
                phase: "completed",
                progress: 100,
                attempt: 1,
                error_code: null,
                result_url: ACCEPTED.result_url,
            });
        }
        if (path === ACCEPTED.result_url) return zipResponse(resultEntries);
        if (init?.method === "POST") return operationResponse;
        throw new Error(`unexpected request: ${path}`);
    }) as unknown as typeof fetch;
    return requests;
}

function mockStatus(status: string, errorCode: string | null = null) {
    globalThis.fetch = (async (url: string | URL | Request) => {
        if (String(url) === ACCEPTED.status_url) {
            return jsonResponse({
                job_id: JOB_ID,
                status,
                phase: status === "succeeded" ? "completed" : "detection",
                progress: 0,
                attempt: 1,
                error_code: errorCode,
                result_url: null,
            });
        }
        throw new Error(`unexpected request: ${String(url)}`);
    }) as unknown as typeof fetch;
}

describe("text requests — sync vs accepted job", () => {
    it("keeps a sync mask response synchronous", async () => {
        const payload = { text: "[PERSON_1]", keys: "Физлица:\n  - VALUE-A", stats: AGGREGATE };
        globalThis.fetch = (async () => jsonResponse(payload)) as unknown as typeof fetch;
        const jobs = fakeJobs();

        const response = await postMaskText("VALUE-A");
        const data = await resolveMaskResponse(response, jobs.session);

        expect(data).toEqual(payload);
        expect(jobs.calls).toEqual([]);
        expect(jobs.pollingController.current).toBeNull();
    });

    it("tracks, polls and unpacks an accepted text mask", async () => {
        const requests = mockTerminalJob(jsonResponse(ACCEPTED, 202), {
            "result.json": JSON.stringify({
                text: "[PERSON_1]",
                keys: "Физлица:\n  - VALUE-A",
                stats: AGGREGATE,
            }),
        });
        const jobs = fakeJobs();

        const response = await postMaskText("VALUE-A");
        const data = await resolveMaskResponse(response, jobs.session);

        expect(data.text).toBe("[PERSON_1]");
        expect(data.stats).toEqual(AGGREGATE);
        expect(jobs.calls).toEqual(["track", "end"]);
        expect(jobs.pollingController.current).toBeInstanceOf(AbortController);
        expect(requests).toContain(ACCEPTED.status_url);
        expect(requests).toContain(ACCEPTED.result_url);
    });

    it("keeps a sync unmask response synchronous", async () => {
        globalThis.fetch = (async () =>
            jsonResponse({ text: "VALUE-A" })) as unknown as typeof fetch;
        const jobs = fakeJobs();
        const data = await resolveUnmaskResponse(
            await postUnmaskText("[PERSON_1]", "Физлица:\n  - VALUE-A"),
            jobs.session,
        );
        expect(data.text).toBe("VALUE-A");
        expect(jobs.calls).toEqual([]);
        expect(jobs.pollingController.current).toBeNull();
    });

    it("tracks an accepted text unmask", async () => {
        mockTerminalJob(jsonResponse(ACCEPTED, 202), {
            "result.json": JSON.stringify({ text: "VALUE-A" }),
        });
        const jobs = fakeJobs();
        const data = await resolveUnmaskResponse(
            await postUnmaskText("[PERSON_1]", "Физлица:\n  - VALUE-A"),
            jobs.session,
        );
        expect(data.text).toBe("VALUE-A");
        expect(jobs.calls).toEqual(["track", "end"]);
    });
});

describe("file requests — sync ZIP vs accepted job", () => {
    it("unpacks a sync mask-file ZIP (keys + manifest, no header inference)", async () => {
        globalThis.fetch = (async () => zipResponse(MASK_ZIP)) as unknown as typeof fetch;
        const jobs = fakeJobs();
        const file = new File(["docx"], "doc.docx");
        const response = await postMaskFile(file);

        const bundle = await resolveMaskBundle(response, jobs.session);
        expect(bundle.resultName).toBe("doc (замаскированный).docx");
        expect(bundle.keysName).toBe("doc (ключи демаскировки).yaml");
        expect(await bundle.keys.text()).toContain("VALUE-A");
        expect(bundle.manifest.statistics.total_occurrences).toBe(1);
        expect(jobs.calls).toEqual([]);
    });

    it("unpacks an accepted mask-file job with the same bundle shape", async () => {
        mockTerminalJob(jsonResponse(ACCEPTED, 202), MASK_ZIP);
        const jobs = fakeJobs();
        const response = await postMaskFile(new File(["docx"], "doc.docx"));

        const bundle = await resolveMaskBundle(response, jobs.session);
        expect(bundle.resultName).toBe("doc (замаскированный).docx");
        expect(bundle.manifest.operation).toBe("mask_file");
        expect(jobs.calls).toEqual(["track", "end"]);
    });

    it("sends keys as a form field for file unmasking", async () => {
        let body: FormData | null = null;
        globalThis.fetch = (async (_url: string | URL | Request, init?: RequestInit) => {
            body = init?.body as FormData;
            return zipResponse(RESTORE_ZIP);
        }) as unknown as typeof fetch;
        const jobs = fakeJobs();
        await resolveRestoreBundle(
            await postUnmaskFile(new File(["docx"], "doc.docx"), "Физлица:\n  - VALUE-A"),
            jobs.session,
        );
        expect(body).toBeInstanceOf(FormData);
        expect((body as unknown as FormData).get("keys")).toBe("Физлица:\n  - VALUE-A");
    });

    it("unpacks a sync restore ZIP and its unused_keys", async () => {
        globalThis.fetch = (async () => zipResponse(RESTORE_ZIP)) as unknown as typeof fetch;
        const jobs = fakeJobs();
        const bundle = await resolveRestoreBundle(
            await postUnmaskFile(new File(["docx"], "doc.docx"), "keys"),
            jobs.session,
        );
        expect(bundle.docxName).toBe("doc (демаскированный).docx");
        expect(bundle.manifest.statistics.unused_keys).toEqual(["VALUE-B"]);
        expect(jobs.calls).toEqual([]);
    });

    it("unpacks an accepted restore-file job", async () => {
        mockTerminalJob(jsonResponse(ACCEPTED, 202), RESTORE_ZIP);
        const jobs = fakeJobs();
        const bundle = await resolveRestoreBundle(
            await postUnmaskFile(new File(["docx"], "doc.docx"), "keys"),
            jobs.session,
        );
        expect(bundle.docxName).toBe("doc (демаскированный).docx");
        expect(jobs.calls).toEqual(["track", "end"]);
    });
});

describe("job failures and malformed results", () => {
    it("rejects a failed or expired job with a safe error", async () => {
        mockStatus("failed", "internal_error");
        const jobs = fakeJobs();
        await expect(
            resolveMaskResponse(jsonResponse(ACCEPTED, 202), jobs.session),
        ).rejects.toBeInstanceOf(JobFailedError);

        mockStatus("expired");
        await expect(
            resolveMaskResponse(jsonResponse(ACCEPTED, 202), jobs.session),
        ).rejects.toBeInstanceOf(JobFailedError);
    });

    it("rejects a result ZIP without a manifest on both the sync and job path", async () => {
        globalThis.fetch = (async () =>
            zipResponse({
                "doc.docx": "bytes",
                "doc.yaml": "keys",
            })) as unknown as typeof fetch;
        await expect(
            resolveMaskBundle(
                await postMaskFile(new File(["docx"], "doc.docx")),
                fakeJobs().session,
            ),
        ).rejects.toThrow(/отчёт о результате/i);

        mockTerminalJob(jsonResponse(ACCEPTED, 202), { "doc.docx": "bytes", "doc.yaml": "keys" });
        await expect(
            resolveMaskBundle(
                await postMaskFile(new File(["docx"], "doc.docx")),
                fakeJobs().session,
            ),
        ).rejects.toThrow(/отчёт о результате/i);
    });

    it("does not expose input values in error details", async () => {
        globalThis.fetch = (async () =>
            jsonResponse(
                { detail: "Недопустимое значение VALUE-A" },
                422,
            )) as unknown as typeof fetch;
        const detail = await readErrorDetail(jsonResponse({}, 422));
        expect(detail).toBe("Ошибка сервера (422).");
        const response = await postMaskText("VALUE-A");
        expect(response.status).toBe(422);
        expect(await readErrorDetail(response)).toBe("Недопустимое значение VALUE-A");
        // The upstream detail is user-facing Russian text, not a raw entity.
        expect(operationError(new Error("ValueError"), "fallback")).toBe("ValueError");
        expect(operationError("строка", "fallback")).toBe("fallback");
    });
});
