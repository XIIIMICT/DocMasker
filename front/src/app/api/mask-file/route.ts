import { FILE_TIMEOUT_MS, passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/**
 * Forward a DOCX mask request to the backend. The browser sends a multipart
 * form with the file; we replay it server-side and stream the resulting ZIP
 * (masked .docx + demasking keys) back, with the count headers. Large inputs
 * may be answered with 202 + a job id — the body is streamed verbatim either way.
 */
export async function POST(request: Request) {
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

    const response = await postToBackend(
        "/api/mask-file",
        { method: "POST", body: upload },
        FILE_TIMEOUT_MS,
    );

    return passThrough(response, [
        "X-Masked-Count",
        "X-Masked-Breakdown",
        "X-Docx-Unsupported-Parts",
        "Content-Disposition",
    ]);
}
