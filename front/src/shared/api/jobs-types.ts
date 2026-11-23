/** The 202 body returned by the backend when an input is handled as a job. */
export interface JobAccepted {
    job_id: string;
    status: string;
    status_url: string;
    result_url: string;
}

/** Client-visible job state (safe metadata only — no PII). */
export interface JobStatus {
    job_id: string;
    status: "queued" | "running" | "succeeded" | "failed" | "cancelled" | "expired";
    phase: string;
    progress: number;
    attempt: number;
    error_code: string | null;
    result_url: string | null;
}

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
