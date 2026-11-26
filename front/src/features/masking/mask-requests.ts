import type { RefObject } from "react";

import type { JobAccepted } from "@/shared/api/jobs";
import { waitForJob } from "@/shared/api/jobs";
import type { MaskResponse, UnmaskResponse } from "@/shared/api/mask";
import {
    unpackMaskArchive,
    unpackRestoreArchive,
    type MaskedBundle,
    type RestoredBundle,
} from "@/shared/api/mask-archive";
import {
    jobBundle,
    jobRestoreBundle,
    jobToMaskResponse,
    jobToUnmaskResponse,
} from "@/shared/api/mask-helpers";

/** The job lifecycle surface the request layer needs (no React state of its own). */
export interface JobSession {
    trackAccepted(response: Response): Promise<JobAccepted>;
    endJob(): void;
    pollingController: RefObject<AbortController | null>;
}

/** Extract a Russian error `detail` from a failed proxied response. */
export async function readErrorDetail(response: Response): Promise<string> {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    return payload?.detail ?? `Ошибка сервера (${response.status}).`;
}

/** Uniform error text for every operation boundary (never logs the input). */
export function operationError(error: unknown, fallback: string): string {
    return error instanceof Error ? error.message : fallback;
}

export function postMaskText(text: string): Promise<Response> {
    return fetch("/api/mask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    });
}

export function postMaskFile(file: File): Promise<Response> {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return fetch("/api/mask-file", { method: "POST", body: formData });
}

export function postUnmaskText(text: string, keys: string): Promise<Response> {
    return fetch("/api/unmask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, keys }),
    });
}

export function postUnmaskFile(file: File, keys: string): Promise<Response> {
    if (!file) throw new Error("Файл не выбран.");
    const formData = new FormData();
    formData.append("file", file, file.name);
    formData.append("keys", keys);
    return fetch("/api/unmask-file", { method: "POST", body: formData });
}

/**
 * Normalise a possibly-accepted text-mask response. A 200 stays synchronous;
 * a 202 is tracked, polled with an AbortController and unpacked from
 * `result.json`. Cancellation never removes the server job here — `endJob`
 * only clears the UI handle after the job reached a terminal state.
 */
export async function resolveMaskResponse(
    response: Response,
    jobs: JobSession,
): Promise<MaskResponse> {
    if (response.status !== 202) return (await response.json()) as MaskResponse;
    const accepted = await jobs.trackAccepted(response);
    const controller = new AbortController();
    jobs.pollingController.current = controller;
    const data = await jobToMaskResponse(accepted, controller.signal);
    jobs.endJob();
    return data;
}

/** Normalise a sync file-mask ZIP or a 202 job result into one bundle. */
export async function resolveMaskBundle(
    response: Response,
    jobs: JobSession,
): Promise<MaskedBundle> {
    if (response.status !== 202) return unpackMaskArchive(await response.blob());
    const accepted = await jobs.trackAccepted(response);
    const controller = new AbortController();
    jobs.pollingController.current = controller;
    const jobResult = await waitForJob(accepted, controller.signal);
    jobs.endJob();
    return jobBundle(jobResult);
}

/** Normalise a possibly-accepted text-unmask response. */
export async function resolveUnmaskResponse(
    response: Response,
    jobs: JobSession,
): Promise<UnmaskResponse> {
    if (response.status !== 202) return (await response.json()) as UnmaskResponse;
    const accepted = await jobs.trackAccepted(response);
    const controller = new AbortController();
    jobs.pollingController.current = controller;
    const data = await jobToUnmaskResponse(accepted, controller.signal);
    jobs.endJob();
    return data;
}

/** Normalise a sync restore ZIP or a 202 job result into one bundle. */
export async function resolveRestoreBundle(
    response: Response,
    jobs: JobSession,
): Promise<RestoredBundle> {
    if (response.status !== 202) return unpackRestoreArchive(await response.blob());
    const accepted = await jobs.trackAccepted(response);
    const controller = new AbortController();
    jobs.pollingController.current = controller;
    const bundle = await jobRestoreBundle(accepted, controller.signal);
    jobs.endJob();
    return bundle;
}
