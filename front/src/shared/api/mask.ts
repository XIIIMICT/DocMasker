import type {
    MaskResponse as GeneratedMaskResponse,
    MaskStats,
    UnmaskResponse as GeneratedUnmaskResponse,
} from "@/shared/api/generated/openapi";
import { JSON_TIMEOUT_MS, UPLOAD_TIMEOUT_MS } from "@/shared/api/timeouts";
import { getBackendInternalUrl } from "@/shared/config";
import {
    parseKeys,
    type KeysParseResult,
    type ParsedKeysDocument,
} from "@/shared/masking/keys-parse";

// Re-exported for existing callers/tests; canonical values live in timeouts.ts.
export const PROXY_JSON_TIMEOUT_MS = JSON_TIMEOUT_MS;
export const PROXY_FILE_TIMEOUT_MS = UPLOAD_TIMEOUT_MS;

/** Preserve the backend's correlation id on a proxied JSON response. */
function passJsonHeaders(response: Response): Headers {
    const headers = new Headers({
        "Content-Type": response.headers.get("Content-Type") ?? "application/json",
    });
    const requestId = response.headers.get("X-Request-ID");
    if (requestId) headers.set("X-Request-ID", requestId);
    return headers;
}

export { KEYS_SECTION_TITLES, KEYS_TITLE_LABELS } from "@/shared/api/labels.generated";

// HTTP DTOs are generated from app.openapi() (compile-time only, no runtime
// validation). The aliases keep the established public names for callers.
/** Aggregate counts the backend sends instead of raw entity values. */
export type MaskStatsPayload = MaskStats;
export type MaskResponse = GeneratedMaskResponse;
export type UnmaskResponse = GeneratedUnmaskResponse;

// Keys parsing lives in one pure boundary; this module keeps the established
// public helpers and lets callers hand over an already parsed document.
export { parseKeys, parseSimpleYaml } from "@/shared/masking/keys-parse";
export type { KeysEntry, KeysParseResult, ParsedKeysDocument } from "@/shared/masking/keys-parse";

function documentOf(keys: string | ParsedKeysDocument): ParsedKeysDocument {
    return typeof keys === "string" ? parseKeys(keys) : keys;
}

/**
 * The placeholders a keys document stands for, e.g. `["[PERSON_1]", …]`.
 *
 * Numbers restart at 1 within each section (that is how the backend writes
 * them), so the n-th value of a section is `[LABEL_n]`. Unparseable input
 * yields an empty list rather than throwing — highlighting is a nicety.
 */
export function placeholdersFromKeys(keys: string | ParsedKeysDocument): string[] {
    return keyEntries(keys).map(([placeholder]) => placeholder);
}

/** The original values a keys document carries, in order. */
export function valuesFromKeys(keys: string | ParsedKeysDocument): string[] {
    return keyEntries(keys).map(([, value]) => value);
}

/** `(placeholder, value)` pairs of a keys document, in document order. */
export function keyEntries(keys: string | ParsedKeysDocument): Array<[string, string]> {
    return documentOf(keys).entries.map(({ placeholder, value }): [string, string] => [
        placeholder,
        value,
    ]);
}

/**
 * Validate a demasking keys file on the client, mirroring the backend rules.
 * Accepts the raw YAML or an already parsed result so one edit is parsed once.
 * Returns a Russian error message, or `null` when valid — the form uses it to
 * block the request and mark the field invalid.
 */
export function validateKeysYaml(keys: string | KeysParseResult): string | null {
    const parsed = typeof keys === "string" ? parseKeys(keys) : keys;
    return parsed.valid ? null : parsed.error;
}

/**
 * Forward a text unmask request to the backend (server-side, no CORS).
 * Used by the Next.js API route `/api/unmask`. The response body is streamed
 * through (no full arrayBuffer copy) so large payloads are not buffered twice.
 */
export async function proxyUnmaskRequest(
    text: string,
    keys: string,
    signal?: AbortSignal,
): Promise<Response> {
    const targetUrl = new URL("/api/unmask", getBackendInternalUrl());

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text, keys }),
        signal: signal ?? AbortSignal.timeout(JSON_TIMEOUT_MS),
    });

    return new Response(response.body, {
        headers: passJsonHeaders(response),
        status: response.status,
    });
}

/**
 * Forward a mask request to the backend (server-side, no CORS).
 * Used by the Next.js API route `/api/mask`. Returns the backend's HTTP
 * response verbatim (status + streamed body).
 */
export async function proxyMaskRequest(text: string, signal?: AbortSignal): Promise<Response> {
    const targetUrl = new URL("/api/mask", getBackendInternalUrl());

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text }),
        signal: signal ?? AbortSignal.timeout(JSON_TIMEOUT_MS),
    });

    return new Response(response.body, {
        headers: passJsonHeaders(response),
        status: response.status,
    });
}
