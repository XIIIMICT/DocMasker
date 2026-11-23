import { passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/**
 * Proxy the durable-job API (ADR-0002) to the backend. Short JSON requests use
 * the 15 s timeout; result downloads are streamed through unchanged (including
 * Cache-Control: no-store). The opaque job id is the only path parameter.
 */
async function forward(request: Request, path: string): Promise<Response> {
    const init: RequestInit = { method: request.method };
    if (request.method !== "GET" && request.method !== "HEAD") {
        init.body = await request.arrayBuffer();
        const contentType = request.headers.get("Content-Type");
        if (contentType) init.headers = { "Content-Type": contentType };
    }
    const response = await postToBackend(path, init);
    return passThrough(response, ["Content-Type", "Content-Disposition", "Cache-Control"]);
}

export async function POST(request: Request) {
    return forward(request, "/api/jobs");
}

export async function GET(request: Request, { params }: { params: Promise<{ path: string[] }> }) {
    const { path } = await params;
    return forward(request, `/api/jobs/${path.join("/")}`);
}

export async function DELETE(
    request: Request,
    { params }: { params: Promise<{ path: string[] }> },
) {
    const { path } = await params;
    return forward(request, `/api/jobs/${path.join("/")}`);
}
