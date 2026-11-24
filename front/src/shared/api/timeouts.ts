/**
 * Canonical proxy timeout policy (Phase 6, §8.4).
 *
 * One source of truth for route→timeout mapping. Long operations never hold a
 * single request open: they become durable jobs (202) polled by the client.
 * The global undici dispatcher keeps finite header/body timeouts as a safety
 * net (`instrumentation.ts`); these per-request signals are the real policy.
 */

/** Short JSON mask/unmask requests (small sync work only). */
export const JSON_TIMEOUT_MS = 15_000;
/** Job submit / status / cancel — polling must fail fast and back off. */
export const JOB_TIMEOUT_MS = 15_000;
/** Multipart upload forwarding (bounded by the configured max upload size). */
export const UPLOAD_TIMEOUT_MS = 120_000;
/** Result download/streaming — generous but finite, never unbounded. */
export const RESULT_TIMEOUT_MS = 300_000;
/** Overall client job-wait budget (workflow, not a proxy request timeout). */
export const JOB_WAIT_TIMEOUT_MS = 20 * 60_000;
/** Polling backoff bounds for `waitForJob` (fail fast, then cap the delay). */
export const POLL_BASE_MS = 400;
export const POLL_MAX_MS = 4_000;

/** Which timeout a BFF route must use. */
export type ProxyRoute =
    | "mask"
    | "unmask"
    | "labels"
    | "mask-file"
    | "unmask-file"
    | "job-submit"
    | "job-status"
    | "job-cancel"
    | "job-result";

export function timeoutForRoute(route: ProxyRoute): number {
    switch (route) {
        case "mask":
        case "unmask":
        case "labels":
            return JSON_TIMEOUT_MS;
        case "mask-file":
        case "unmask-file":
            return UPLOAD_TIMEOUT_MS;
        case "job-submit":
        case "job-status":
        case "job-cancel":
            return JOB_TIMEOUT_MS;
        case "job-result":
            return RESULT_TIMEOUT_MS;
    }
}
