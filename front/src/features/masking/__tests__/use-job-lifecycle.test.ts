import { afterEach, beforeEach, describe, expect, it } from "bun:test";

import { act, renderHook, waitFor } from "@testing-library/react";
import JSZip from "jszip";

import { ACTIVE_JOB_KEY, type JobAccepted } from "@/shared/api/jobs";

import { useJobLifecycle } from "../use-job-lifecycle";
import { useModeDrafts } from "../use-mode-drafts";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

async function zipResponse(entries: Record<string, string>): Promise<Response> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(entries)) zip.file(name, content);
    return new Response(await zip.generateAsync({ type: "blob" }), { status: 200 });
}

function setup(seed?: { mode: "mask" | "unmask"; accepted: JobAccepted }, rawSeed?: string) {
    window.localStorage.clear();
    window.sessionStorage.clear();
    if (rawSeed !== undefined) window.sessionStorage.setItem(ACTIVE_JOB_KEY, rawSeed);
    else if (seed) window.sessionStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify(seed));
    const hook = renderHook(() => {
        const drafts = useModeDrafts();
        const jobs = useJobLifecycle({
            mode: drafts.mode,
            activeJob: drafts.state.activeJob,
            update: drafts.update,
            setStateByMode: drafts.setStateByMode,
        });
        return { drafts, jobs };
    });
    return { hook };
}

const cancelRequests: Array<{ url: string; method: string | undefined }> = [];

function mockCancel(status: number) {
    cancelRequests.length = 0;
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
        cancelRequests.push({ url: String(url), method: init?.method });
        return new Response(null, { status });
    }) as unknown as typeof fetch;
}

function mockSucceededJob(entries: Record<string, string>) {
    globalThis.fetch = (async (url: string | URL | Request) => {
        const path = String(url);
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
        if (path === ACCEPTED.result_url) return zipResponse(entries);
        throw new Error(`unexpected request: ${path}`);
    }) as unknown as typeof fetch;
}

function mockJobStatus(status: string, errorCode: string | null = null) {
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

async function trackActiveJob(hook: ReturnType<typeof setup>["hook"]) {
    await act(async () => {
        await hook.result.current.jobs.trackAccepted(jsonResponse(ACCEPTED, 202));
    });
}

beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
});

afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH;
    window.sessionStorage.clear();
    window.localStorage.clear();
});

describe("useJobLifecycle — active job handle", () => {
    it("persists only the opaque job id, never input or keys", async () => {
        const { hook } = setup();
        act(() =>
            hook.result.current.drafts.update({ input: "INPUT-MARKER", demaskKeys: "KEY-MARKER" }),
        );
        await trackActiveJob(hook);

        expect(hook.result.current.drafts.state.activeJob).toEqual(ACCEPTED);
        const raw = window.sessionStorage.getItem(ACTIVE_JOB_KEY)!;
        expect(raw).toContain(JOB_ID);
        expect(raw).not.toContain("INPUT-MARKER");
        expect(raw).not.toContain("KEY-MARKER");
        expect(raw).not.toContain("doc.docx");
        expect(JSON.parse(raw)).toEqual({ mode: "mask", accepted: ACCEPTED });
    });

    it("stops polling and clears the handle on an accepted cancellation", async () => {
        const { hook } = setup();
        await trackActiveJob(hook);
        act(() => hook.result.current.drafts.update({ busy: true }));
        const controller = new AbortController();
        hook.result.current.jobs.pollingController.current = controller;
        mockCancel(204);

        await act(async () => {
            await hook.result.current.jobs.onCancelJob();
        });
        expect(cancelRequests).toEqual([{ url: `/api/jobs/${JOB_ID}`, method: "DELETE" }]);
        expect(controller.signal.aborted).toBe(true);
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
        expect(hook.result.current.drafts.state.busy).toBe(false);
        expect(hook.result.current.drafts.state.error).toBe("Задание отменено.");
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("reports a job that had already reached a terminal state", async () => {
        const { hook } = setup();
        await trackActiveJob(hook);
        mockCancel(404);

        await act(async () => {
            await hook.result.current.jobs.onCancelJob();
        });
        expect(hook.result.current.drafts.state.error).toBe(
            "Задание уже завершено. Проверьте результат.",
        );
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
    });

    it("keeps polling and the handle when cancellation cannot be confirmed", async () => {
        const { hook } = setup();
        await trackActiveJob(hook);
        act(() => hook.result.current.drafts.update({ busy: true }));
        mockCancel(500);

        await act(async () => {
            await hook.result.current.jobs.onCancelJob();
        });
        expect(hook.result.current.drafts.state.activeJob).toEqual(ACCEPTED);
        expect(hook.result.current.drafts.state.busy).toBe(true);
        expect(hook.result.current.drafts.state.error).toBe(
            "Не удалось подтвердить отмену задания. Повторите попытку.",
        );
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).not.toBeNull();
    });

    it("finish clears the controller, the handle and the busy flag", async () => {
        const { hook } = setup();
        await trackActiveJob(hook);
        act(() => hook.result.current.drafts.update({ busy: true }));
        const controller = new AbortController();
        hook.result.current.jobs.pollingController.current = controller;

        act(() => hook.result.current.jobs.finish());
        expect(hook.result.current.jobs.pollingController.current).toBeNull();
        expect(controller.signal.aborted).toBe(false); // finish does not cancel the server job
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
        expect(hook.result.current.drafts.state.busy).toBe(false);
    });

    it("an in-flight job finishes into the mode where it started", async () => {
        const { hook } = setup();
        await trackActiveJob(hook);
        act(() => hook.result.current.drafts.update({ busy: true }));
        const capturedFinish = hook.result.current.jobs.finish;
        const capturedEndJob = hook.result.current.jobs.endJob;
        act(() => hook.result.current.drafts.onModeChange("unmask"));

        act(() => {
            capturedEndJob();
            capturedFinish();
        });
        const drafts = hook.result.current.drafts.stateByMode;
        expect(drafts.mask.activeJob).toBeNull();
        expect(drafts.mask.busy).toBe(false);
        expect(drafts.unmask.activeJob).toBeNull();
        expect(drafts.unmask.busy).toBe(false);
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });
});

describe("useJobLifecycle — reload-resume", () => {
    it("restores a text mask job into the originating mask draft", async () => {
        mockSucceededJob({
            "result.json": JSON.stringify({
                text: "[PERSON_1]",
                keys: "Физлица:\n  - VALUE-A",
                stats: AGGREGATE,
            }),
        });
        const { hook } = setup({ mode: "mask", accepted: ACCEPTED });

        expect(hook.result.current.drafts.state.activeJob).toEqual(ACCEPTED);
        expect(hook.result.current.drafts.state.busy).toBe(true);
        await waitFor(() => expect(hook.result.current.drafts.state.result).toBe("[PERSON_1]"));

        const state = hook.result.current.drafts.state;
        expect(state.keys).toContain("VALUE-A");
        expect(state.stats?.total).toBe(1);
        expect(state.activeJob).toBeNull();
        expect(state.busy).toBe(false);
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("restores a text unmask job into the unmask draft and leaves masking untouched", async () => {
        mockSucceededJob({ "result.json": JSON.stringify({ text: "[PERSON_1]" }) });
        const { hook } = setup({ mode: "unmask", accepted: ACCEPTED });

        await waitFor(() =>
            expect(hook.result.current.drafts.stateByMode.unmask.restored).toBe("[PERSON_1]"),
        );

        const drafts = hook.result.current.drafts.stateByMode;
        expect(drafts.unmask.activeJob).toBeNull();
        expect(drafts.unmask.busy).toBe(false);
        expect(drafts.mask.result).toBe("");
        expect(drafts.mask.restored).toBe("");
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("clears cleanly when a resumed file job has no result.json", async () => {
        mockSucceededJob({ "doc.docx": "bytes" });
        const { hook } = setup({ mode: "mask", accepted: ACCEPTED });

        await waitFor(() =>
            expect(hook.result.current.drafts.state.error).toBe(
                "Задание завершено. Повторите операцию, чтобы скачать файл.",
            ),
        );
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
        expect(hook.result.current.drafts.state.busy).toBe(false);
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("surfaces a failed job without PII", async () => {
        mockJobStatus("failed", "internal_error");
        const { hook } = setup({ mode: "mask", accepted: ACCEPTED });
        await waitFor(() => expect(hook.result.current.drafts.state.error).toBeTruthy());
        expect(hook.result.current.drafts.state.error).toBe(
            "Не удалось обработать задание. Попробуйте ещё раз.",
        );
        expect(hook.result.current.drafts.state.error).not.toContain("VALUE-A");
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("surfaces an expired job and clears the handle", async () => {
        mockJobStatus("expired");
        const { hook } = setup({ mode: "unmask", accepted: ACCEPTED });
        // The job started in demasking mode, so the error belongs to that
        // draft even though the workflow currently displays the mask draft.
        await waitFor(() =>
            expect(hook.result.current.drafts.stateByMode.unmask.error).toBe(
                "Срок хранения результата истёк.",
            ),
        );
        expect(hook.result.current.drafts.stateByMode.unmask.activeJob).toBeNull();
        expect(hook.result.current.drafts.stateByMode.unmask.busy).toBe(false);
        expect(window.sessionStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
    });

    it("ignores malformed sessionStorage payloads", () => {
        const { hook } = setup(undefined, "{ not json");
        expect(hook.result.current.drafts.state.activeJob).toBeNull();
        expect(hook.result.current.drafts.state.busy).toBe(false);
    });
});
