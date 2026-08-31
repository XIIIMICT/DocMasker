import { getBackendInternalUrl } from "@/shared/config";

export interface MaskedEntity {
    label: string;
    text: string;
    start: number;
    end: number;
    score: number;
}

export interface MaskResponse {
    text: string;
    masked_entities: MaskedEntity[];
}

/**
 * Forward a mask request to the backend (server-side, no CORS).
 * Used by the Next.js API route `/api/mask`. Returns the backend's HTTP
 * response verbatim so status codes and the body pass through.
 */
export async function proxyMaskRequest(text: string): Promise<Response> {
    const targetUrl = new URL("/api/mask", getBackendInternalUrl());

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text }),
    });

    const responseContentType = response.headers.get("Content-Type") ?? "application/json";
    const responseBody = await response.arrayBuffer();
    const headers = new Headers({ "Content-Type": responseContentType });
    return new Response(responseBody, { headers, status: response.status });
}
