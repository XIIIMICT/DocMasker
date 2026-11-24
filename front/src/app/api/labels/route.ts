import { passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/** Proxy the canonical label catalogue (GET /api/labels) to the backend. */
export async function GET() {
    const response = await postToBackend("/api/labels", { method: "GET" }, "labels");
    return passThrough(response);
}
