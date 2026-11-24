import JSZip from "jszip";

import { JobFailedError } from "@/shared/api/jobs-types";
import type { JobAccepted, JobResult, JobStatus } from "@/shared/api/jobs-types";
import { JOB_WAIT_TIMEOUT_MS, POLL_BASE_MS, POLL_MAX_MS } from "@/shared/api/timeouts";

export { JobFailedError } from "@/shared/api/jobs-types";
export type { JobAccepted, JobResult, JobStatus } from "@/shared/api/jobs-types";

/** sessionStorage key for resuming an active job in the same tab after reload. */
export const ACTIVE_JOB_KEY = "docmasker.activeJob";

/** Request server-side cancellation. Aborting polling alone must NOT cancel it. */
export async function cancelJob(accepted: JobAccepted): Promise<void> {
    try {
        await fetch(`/api/jobs/${encodeURIComponent(accepted.job_id)}`, { method: "DELETE" });
    } catch {
        /* best effort: the server job is still fenced by its lease/expiry */
    }
}

function buildResult(entries: Map<string, Blob>): JobResult {
    return {
        entries,
        entry(extension: string) {
            for (const [name, value] of entries) {
                if (name.toLowerCase().endsWith(extension)) return value;
            }
            return null;
        },
        async json<T>() {
            const file = this.entry(".json");
            if (!file) throw new JobFailedError("empty_result");
            return JSON.parse(await file.text()) as T;
        },
    };
}

/**
 * Poll a job until it reaches a terminal state, then download and unpack its
 * result. Uses exponential backoff and a bounded attempt count; the caller's
 * AbortSignal cancels polling (e.g. when the user switches modes). Job
 * results are one flat ZIP whose entries are turned into Blobs (no full
 * archive copy kept in memory).
 */
export async function waitForJob(accepted: JobAccepted, signal?: AbortSignal): Promise<JobResult> {
    let delay = POLL_BASE_MS;
    const deadline = Date.now() + JOB_WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
        if (signal?.aborted) throw new JobFailedError("cancelled");
        const statusResponse = await fetch(accepted.status_url, { signal, cache: "no-store" });
        if (!statusResponse.ok) throw new JobFailedError("status_unavailable");
        const status = (await statusResponse.json()) as JobStatus;
        if (status.status === "succeeded" && status.result_url) {
            const resultResponse = await fetch(status.result_url, { signal, cache: "no-store" });
            if (!resultResponse.ok) throw new JobFailedError("result_unavailable");
            const zip = await JSZip.loadAsync(await resultResponse.blob());
            const entries = new Map<string, Blob>();
            for (const [name, entry] of Object.entries(zip.files)) {
                if (!entry.dir) entries.set(name, await entry.async("blob"));
            }
            return buildResult(entries);
        }
        if (
            status.status === "failed" ||
            status.status === "cancelled" ||
            status.status === "expired"
        ) {
            throw new JobFailedError(status.error_code ?? status.status);
        }
        await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(resolve, delay);
            signal?.addEventListener(
                "abort",
                () => {
                    clearTimeout(timer);
                    reject(new JobFailedError("cancelled"));
                },
                { once: true },
            );
        });
        delay = Math.min(delay * 1.5, POLL_MAX_MS);
    }
    throw new JobFailedError("timeout");
}
