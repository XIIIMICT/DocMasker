import { PROXY_FILE_TIMEOUT_MS, PROXY_JSON_TIMEOUT_MS } from "@/shared/api/mask";
import { getBackendInternalUrl } from "@/shared/config";

/** Forward the backend response as a Web Stream (no full arrayBuffer copy). */
export function passThrough(response: Response, forwardedHeaders: string[] = []): Response {
    const headers = new Headers({
        "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
    });
    for (const name of forwardedHeaders) {
        const value = response.headers.get(name);
        if (value) headers.set(name, value);
    }
    return new Response(response.body, { headers, status: response.status });
}

/** POST to the backend with a bounded timeout; short for JSON, longer for files. */
export async function postToBackend(
    path: string,
    init: RequestInit,
    timeoutMs = PROXY_JSON_TIMEOUT_MS,
): Promise<Response> {
    const targetUrl = new URL(path, getBackendInternalUrl());
    return fetch(targetUrl, {
        ...init,
        cache: "no-store",
        signal: AbortSignal.timeout(timeoutMs),
    });
}

/** The bounded timeout used by the synchronous file endpoints. */
export const FILE_TIMEOUT_MS = PROXY_FILE_TIMEOUT_MS;
