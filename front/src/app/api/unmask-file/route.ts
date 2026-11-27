import { randomUUID } from "node:crypto";

import { passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/**
 * Stream a DOCX unmask request to the backend (§8.5).
 *
 * The original multipart body (file + keys) is forwarded unchanged as a Web
 * Stream with duplex:"half" — no request.formData() copy and no rebuilt
 * FormData. Keys/package validation stays on the backend; the client validator
 * already blocks malformed keys before submission.
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
        "/api/unmask-file",
        {
            method: "POST",
            headers: { "Content-Type": contentType },
            body: request.body,
            duplex: "half",
        } as RequestInit,
        "unmask-file",
        request.signal,
    );
    return passThrough(response, [
        "Content-Disposition",
        "Cache-Control",
        "X-Request-ID",
        "Retry-After",
    ]);
}
