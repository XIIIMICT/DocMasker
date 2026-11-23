/**
 * Pure helpers shared by the masking/demasking workflows.
 *
 * No React imports: these adapters turn backend/job responses into the shapes
 * the UI panes expect. Kept out of `masker-page.tsx` so both workflows can use
 * one implementation (stage-5 decomposition).
 */

import type { JobAccepted, JobResult } from "@/shared/api/jobs";
import { waitForJob } from "@/shared/api/jobs";
import type { MaskResponse, UnmaskResponse } from "@/shared/api/mask";
import type { unpackMaskArchive } from "@/shared/api/mask-archive";

/** Human-readable names for the safe unsupported-OOXML-part codes. */
export const UNSUPPORTED_PART_LABELS: Record<string, string> = {
    footnotes: "сноски",
    endnotes: "концевые сноски",
    comments: "комментарии",
    "text-boxes": "текстовые блоки",
    "tracked-changes": "исправления",
    "hidden-text": "скрытый текст",
    "content-controls": "элементы управления содержимым",
    "doc-properties": "свойства документа",
};

/** Parse the safe `X-Docx-Unsupported-Parts` code list from the response. */
export function parseDocxWarning(header: string | null): string[] | null {
    if (!header) return null;
    const codes = header
        .split(",")
        .map((code) => code.trim())
        .filter(Boolean);
    return codes.length > 0 ? codes : null;
}

export function docxWarningText(codes: string[]): string {
    const names = codes.map((code) => UNSUPPORTED_PART_LABELS[code] ?? code);
    return `Внимание: документ содержит части, которые не маскируются (${names.join(", ")}). Скачанный файл не является полной анонимизацией.`;
}

/** Parse the backend's `X-Masked-Breakdown` header (a JSON label→count map). */
export function parseBreakdown(header: string | null): Record<string, number> {
    if (!header) return {};
    try {
        const parsed = JSON.parse(header) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            return parsed as Record<string, number>;
        }
    } catch {
        /* malformed header — show no breakdown */
    }
    return {};
}

/** Convert a text-mask job's result.json into the sync MaskResponse shape. */
export async function jobToMaskResponse(accepted: JobAccepted): Promise<MaskResponse> {
    const payload = await (
        await waitForJob(accepted)
    ).json<{
        text: string;
        keys: string;
        stats: MaskResponse["stats"];
    }>();
    return { text: payload.text, keys: payload.keys, stats: payload.stats };
}

export async function jobToUnmaskResponse(accepted: JobAccepted): Promise<UnmaskResponse> {
    const payload = await (await waitForJob(accepted)).json<{ text: string }>();
    return { text: payload.text };
}

type MaskBundle = Awaited<ReturnType<typeof unpackMaskArchive>>;

/** Build the two-artifact bundle from a file job's flat result ZIP. */
export async function jobBundle(
    result: JobResult,
    docxExtension: string,
    keysExtension: string,
): Promise<MaskBundle> {
    const docx = result.entry(docxExtension);
    const keys = result.entry(keysExtension);
    if (!docx || !keys) throw new Error("Не удалось получить результат задания.");
    const nameFor = (extension: string) => {
        for (const name of result.entries.keys()) {
            if (name.toLowerCase().endsWith(extension)) return name;
        }
        return `result${extension}`;
    };
    return {
        result: docx,
        resultName: nameFor(docxExtension),
        keys,
        keysName: nameFor(keysExtension),
    };
}

export interface RestoreBundle {
    docx: Blob;
    docxName: string;
    manifestName: string;
    manifest: { unused_keys: string[] };
}

/** Build the restored-file bundle from an unmask_file job result ZIP. */
export async function jobRestoreBundle(accepted: JobAccepted): Promise<RestoreBundle> {
    const result = await waitForJob(accepted);
    const docx = result.entry(".docx");
    const manifest = result.entry(".json");
    if (!docx || !manifest) throw new Error("Не удалось получить результат задания.");
    let unused: string[] = [];
    try {
        const parsed = JSON.parse(await manifest.text()) as { unused_keys?: unknown };
        if (Array.isArray(parsed.unused_keys)) unused = parsed.unused_keys as string[];
    } catch {
        /* malformed manifest - treat as no unused keys */
    }
    let name = "restored.docx";
    for (const key of result.entries.keys()) {
        if (key.toLowerCase().endsWith(".docx")) {
            name = key;
            break;
        }
    }
    return {
        docx,
        docxName: name,
        manifestName: "manifest.json",
        manifest: { unused_keys: unused },
    };
}
