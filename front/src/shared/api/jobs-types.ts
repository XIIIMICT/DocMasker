import type {
    JobAccepted as GeneratedJobAccepted,
    JobStatusResponse,
} from "@/shared/api/generated/openapi";

// Generated HTTP DTOs (compile-time only — no runtime validation). Local names
// stay stable for existing callers; ZIP-internal types below are not HTTP DTOs.
/** The 202 body returned by the backend when an input is handled as a job. */
export type JobAccepted = GeneratedJobAccepted;

/** Client-visible job state (safe metadata only — no PII). */
export type JobStatus = JobStatusResponse;

export class JobFailedError extends Error {
    constructor(public readonly code: string) {
        super(
            code === "cancelled"
                ? "Задание отменено."
                : code === "expired"
                  ? "Срок хранения результата истёк."
                  : "Не удалось обработать задание. Попробуйте ещё раз.",
        );
    }
}

/** A completed job's result archive, unpacked into named Blobs. */
export interface JobResult {
    entries: Map<string, Blob>;
    /** The first entry with the given extension, or null. */
    entry(extension: string): Blob | null;
    /** Parse the JSON result of a text job. */
    json<T>(): Promise<T>;
}
