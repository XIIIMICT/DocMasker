import { PROXY_JSON_TIMEOUT_MS } from "@/shared/api/mask";
import { passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/** Proxy the canonical label catalogue (GET /api/labels) to the backend. */
export async function GET() {
    const response = await postToBackend("/api/labels", { method: "GET" }, PROXY_JSON_TIMEOUT_MS);
    return passThrough(response);
}
