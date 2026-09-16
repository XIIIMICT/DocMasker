import { getBackendInternalUrl } from "@/shared/config";

export const dynamic = "force-dynamic";
// No serverless-style execution ceiling: a large DOCX must be allowed to finish.
export const maxDuration = 3600;

/**
 * Forward a DOCX mask request to the backend. The browser sends a multipart
 * form with the file; we replay the request server-side to the back and return
 * the resulting ZIP (masked .docx + demasking keys) verbatim, plus the
 * X-Masked-Count / X-Masked-Breakdown headers. The browser unpacks the archive.
 */
export async function POST(request: Request) {
    const targetUrl = new URL("/api/mask-file", getBackendInternalUrl());

    // Rebuild the multipart body from formData so the backend can read it.
    const formData = await request.formData();
    const file = formData.get("file");
    if (!(file instanceof File)) {
        return Response.json({ detail: "Файл не передан." }, { status: 422 });
    }
    if (!file.name.toLowerCase().endsWith(".docx")) {
        return Response.json({ detail: "Файл должен иметь расширение .docx." }, { status: 422 });
    }

    const upload = new FormData();
    upload.append("file", file, file.name);

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        body: upload,
    });

    const forwarded = new Headers({
        "Content-Type": response.headers.get("Content-Type") ?? "application/octet-stream",
    });
    for (const header of ["X-Masked-Count", "X-Masked-Breakdown", "Content-Disposition"]) {
        const value = response.headers.get(header);
        if (value) forwarded.set(header, value);
    }

    return new Response(await response.arrayBuffer(), {
        headers: forwarded,
        status: response.status,
    });
}
