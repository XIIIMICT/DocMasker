import { passThrough, postToBackend } from "@/shared/api/proxy";
import type { ProxyRoute } from "@/shared/api/timeouts";

export const dynamic = "force-dynamic";

/**
 * Proxy the durable-job API (ADR-0002) to the backend. Short JSON requests use
 * the 15 s timeout; result downloads are streamed through unchanged (including
 * Cache-Control: no-store) with the finite result timeout. The opaque job id is
 * the only path parameter.
 */
async function forward(request: Request, path: string, route: ProxyRoute): Promise<Response> {
    const contentType = request.headers.get("Content-Type");
    const init: RequestInit = { method: request.method };
    if (request.method !== "GET" && request.method !== "HEAD") {
        // Stream the body instead of buffering it into an ArrayBuffer (§8.5).
        init.body = request.body;
        if (contentType) init.headers = { "Content-Type": contentType };
    }
    const response = await postToBackend(path, init, route, request.signal);
    return passThrough(response, [
        "Content-Type",
        "Content-Disposition",
        "Cache-Control",
        "X-Request-ID",
        "Retry-After",
    ]);
}

export async function POST(request: Request) {
    return forward(request, "/api/jobs", "job-submit");
}

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
    const { path } = await params;
    const joined = path.join("/");
    const route: ProxyRoute = joined.endsWith("/result") ? "job-result" : "job-status";
    return forward(request, `/api/jobs/${joined}`, route);
}

export async function DELETE(
    request: Request,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(request, `/api/jobs/${path.join("/")}`, "job-cancel");
}
