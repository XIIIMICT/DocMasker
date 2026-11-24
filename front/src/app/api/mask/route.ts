import { randomUUID } from "node:crypto";

import { proxyMaskRequest } from "@/shared/api/mask";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
    let body: { text?: unknown };
    try {
        body = (await request.json()) as { text?: unknown };
    } catch {
        return Response.json(
            { code: "bad_json", detail: "Некорректный JSON.", request_id: randomUUID() },
            { status: 400 },
        );
    }

    if (typeof body.text !== "string" || body.text.trim().length === 0) {
        return Response.json(
            { code: "empty_text", detail: "Текст пуст.", request_id: randomUUID() },
            { status: 422 },
        );
    }

    return proxyMaskRequest(body.text, request.signal);
}
