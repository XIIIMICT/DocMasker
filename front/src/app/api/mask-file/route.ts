import { randomUUID } from "node:crypto";

import { passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/**
 * Stream a DOCX mask request to the backend (§8.5).
 *
 * The original multipart body (content type + boundary) is forwarded unchanged
 * as a Web Stream with duplex:"half" — no request.formData() copy and no
 * rebuilt FormData. Filename/package validation stays on the backend (the
 * browser proxy is not a trust boundary). Large inputs may be answered with
 * 202 + a job id — the body is streamed verbatim either way.
 */
export async function POST(request: Request) {
    const contentType = request.headers.get("Content-Type");
    if (!contentType || !contentType.toLowerCase().startsWith("multipart/form-data")) {
        return Response.json(
            {
                code: "invalid_multipart",
                detail: "Ожидается multipart-запрос с файлом.",
                request_id: randomUUID(),
            },
            { status: 415 },
        );
    }
    const response = await postToBackend(
        "/api/mask-file",
        {
            method: "POST",
            headers: { "Content-Type": contentType },
            body: request.body,
            // Required by the Fetch spec when the body is a ReadableStream.
            duplex: "half",
        } as RequestInit,
        "mask-file",
        request.signal,
    );
    return passThrough(response, [
        "Content-Disposition",
        "Cache-Control",
        "X-Request-ID",
        "Retry-After",
    ]);
}
