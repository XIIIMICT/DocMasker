import { FILE_TIMEOUT_MS, passThrough, postToBackend } from "@/shared/api/proxy";

export const dynamic = "force-dynamic";

/**
 * Forward a DOCX unmask request to the backend. The browser sends a multipart
 * form with the masked file and the keys (YAML text); we replay it server-side
 * and stream the resulting ZIP (restored .docx + report manifest) back.
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

    const response = await postToBackend(
        "/api/unmask-file",
        { method: "POST", body: upload },
        FILE_TIMEOUT_MS,
    );

    return passThrough(response, ["Content-Disposition", "Cache-Control"]);
}
