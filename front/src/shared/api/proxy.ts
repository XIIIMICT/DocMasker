import { timeoutForRoute, type ProxyRoute } from "@/shared/api/timeouts";
import { getBackendInternalUrl } from "@/shared/config";

/** Forward the backend response as a Web Stream (no full arrayBuffer copy). */
export function passThrough(response: Response, forwardedHeaders: string[] = []): Response {
    const headers = new Headers({
        "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
        // Backend `/api/*` responses are uncacheable; keep that policy for
        // status, job and ZIP downloads too (per-route headers still forwarded).
        "Cache-Control": response.headers.get("Cache-Control") ?? "no-store",
        Pragma: response.headers.get("Pragma") ?? "no-cache",
    });
    for (const name of forwardedHeaders) {
        const value = response.headers.get(name);
        if (value) headers.set(name, value);
    }
    // Keep the backend's request-id / error headers when present.
    const requestId = response.headers.get("X-Request-ID");
    if (requestId) headers.set("X-Request-ID", requestId);
    return new Response(response.body, { headers, status: response.status });
}

/**
 * POST/GET to the backend with the canonical per-route timeout (§8.4).
 *
 * `init.body` may be a stream (multipart forwarded unchanged); the caller's
 * AbortSignal is forwarded where safe so an aborted client upload does not
 * leave a staged artifact. No globally unbounded timeouts.
 */
export async function postToBackend(
    path: string,
    init: RequestInit,
    route: ProxyRoute = "mask",
    signal?: AbortSignal,
): Promise<Response> {
    const targetUrl = new URL(path, getBackendInternalUrl());
    const timeout = timeoutForRoute(route);
    const combined = signal
        ? AbortSignal.any([signal, AbortSignal.timeout(timeout)])
        : AbortSignal.timeout(timeout);
    return fetch(targetUrl, {
        ...init,
        cache: "no-store",
        signal: combined,
    });
}
