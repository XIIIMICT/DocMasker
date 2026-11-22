import { getBackendInternalUrl } from "@/shared/config";

export const dynamic = "force-dynamic";
// No serverless-style execution ceiling: a large DOCX must be allowed to finish.
export const maxDuration = 3600;

/**
 * Forward a DOCX unmask request to the backend. The browser sends a multipart
 * form with the masked file and the keys (YAML text); we replay it server-side
 * to the back and return the restored .docx verbatim.
 */
export async function POST(request: Request) {
    const formData = await request.formData();
    const file = formData.get("file");
    const keys = formData.get("keys");
    if (!(file instanceof File)) {
        return Response.json({ detail: "Файл не передан." }, { status: 422 });
    }
    if (typeof keys !== "string" || keys.trim().length === 0) {
        return Response.json({ detail: "Ключи демаскировки не переданы." }, { status: 422 });
    }
    if (!file.name.toLowerCase().endsWith(".docx")) {
        return Response.json({ detail: "Файл должен иметь расширение .docx." }, { status: 422 });
    }

    const upload = new FormData();
    upload.append("file", file, file.name);
    upload.append("keys", keys);

    const response = await fetch(new URL("/api/unmask-file", getBackendInternalUrl()), {
        cache: "no-store",
        method: "POST",
        body: upload,
    });

    const forwarded = new Headers({
        "Content-Type":
            response.headers.get("Content-Type") ??
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    const disposition = response.headers.get("Content-Disposition");
    if (disposition) forwarded.set("Content-Disposition", disposition);
    const unused = response.headers.get("X-Unused-Keys");
    if (unused) forwarded.set("X-Unused-Keys", unused);

    return new Response(await response.arrayBuffer(), {
        headers: forwarded,
        status: response.status,
    });
}
