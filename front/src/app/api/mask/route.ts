import { proxyMaskRequest } from "@/shared/api/mask";

export const dynamic = "force-dynamic";
// No serverless-style execution ceiling: large inputs must be allowed to finish.
export const maxDuration = 3600;

export async function POST(request: Request) {
    let body: { text?: unknown };
    try {
        body = (await request.json()) as { text?: unknown };
    } catch {
        return Response.json({ detail: "Некорректный JSON." }, { status: 400 });
    }

    if (typeof body.text !== "string" || body.text.trim().length === 0) {
        return Response.json({ detail: "Текст пуст." }, { status: 422 });
    }

    return proxyMaskRequest(body.text);
}
