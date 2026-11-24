import { randomUUID } from "node:crypto";

import { proxyUnmaskRequest } from "@/shared/api/mask";

export const dynamic = "force-dynamic";

/**
 * Forward an unmask request to the backend. The browser sends JSON with the
 * masked text and its keys; the back restores it and we pass the response
 * through verbatim (a 202 job acceptance is streamed through too).
 */
export async function POST(request: Request) {
    const payload = (await request.json().catch(() => null)) as {
        text?: unknown;
        keys?: unknown;
    } | null;
    if (!payload || typeof payload.text !== "string" || typeof payload.keys !== "string") {
        return Response.json(
            {
                code: "validation_error",
                detail: "Нужны поля «text» и «keys».",
                request_id: randomUUID(),
            },
            { status: 422 },
        );
    }

    return proxyUnmaskRequest(payload.text, payload.keys, request.signal);
}
