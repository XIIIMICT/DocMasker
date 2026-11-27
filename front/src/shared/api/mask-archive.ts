import { loadJsZip } from "@/shared/api/lazy-zip";
import {
    parseResultManifest,
    type ManifestOperation,
    type ResultManifest,
} from "@/shared/api/manifest-validation";

export { ManifestValidationError } from "@/shared/api/manifest-validation";
export type { ManifestErrorCode, ResultManifest } from "@/shared/api/manifest-validation";

export const MANIFEST_NAME = "manifest.json";

/** Unpack already-extracted ZIP entries (used by job results, §7.1 parity). */
export function maskBundleFromEntries(
    entries: Map<string, Blob>,
    manifest: ResultManifest,
): MaskedBundle {
    const resultEntry = [...entries].find(([name]) => name.toLowerCase().endsWith(".docx"));
    const keysEntry = [...entries].find(([name]) => name.toLowerCase().endsWith(".yaml"));
    if (!resultEntry || !keysEntry) throw new Error("Не удалось получить результат задания.");
    return {
        result: resultEntry[1],
        resultName: resultEntry[0],
        keys: keysEntry[1],
        keysName: keysEntry[0],
        manifest,
    };
}

export function restoreBundleFromEntries(
    entries: Map<string, Blob>,
    manifest: ResultManifest,
): RestoredBundle {
    const docxEntry = [...entries].find(([name]) => name.toLowerCase().endsWith(".docx"));
    if (!docxEntry) throw new Error("Не удалось получить результат задания.");
    const manifestName =
        [...entries].find(([name]) => name.toLowerCase() === MANIFEST_NAME)?.[0] ?? MANIFEST_NAME;
    return { docx: docxEntry[1], docxName: docxEntry[0], manifestName, manifest };
}

export function validateManifestText(text: string, operation: ManifestOperation): ResultManifest {
    return parseResultManifest(text, operation);
}

/** The masked document, its keys and the validated manifest. */
export interface MaskedBundle {
    result: Blob;
    resultName: string;
    keys: Blob;
    keysName: string;
    manifest: ResultManifest;
}

/**
 * Unpack the flat ZIP returned by `/api/mask-file`. The manifest is the single
 * authoritative source of statistics/coverage — no inference from headers or
 * from parsing key placeholders.
 */
export async function unpackMaskArchive(archive: Blob): Promise<MaskedBundle> {
    const JSZip = await loadJsZip();
    const zip = await JSZip.loadAsync(archive);
    const entries = Object.values(zip.files).filter((entry) => !entry.dir);
    const resultEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".docx"));
    const keysEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".yaml"));
    const manifestEntry = entries.find((entry) => entry.name.toLowerCase() === MANIFEST_NAME);
    if (!resultEntry || !keysEntry || !manifestEntry) {
        throw new Error("Архив не содержит замаскированный файл, ключи и отчёт о результате.");
    }
    const manifest = parseResultManifest(await manifestEntry.async("text"), "mask_file");
    return {
        result: await resultEntry.async("blob"),
        resultName: resultEntry.name,
        keys: await keysEntry.async("blob"),
        keysName: keysEntry.name,
        manifest,
    };
}

/** The restored document plus the validated manifest (with unused_keys). */
export interface RestoredBundle {
    docx: Blob;
    docxName: string;
    manifestName: string;
    manifest: ResultManifest;
}

/** Unpack the flat ZIP returned by `/api/unmask-file`. */
export async function unpackRestoreArchive(archive: Blob): Promise<RestoredBundle> {
    const JSZip = await loadJsZip();
    const zip = await JSZip.loadAsync(archive);
    const entries = Object.values(zip.files).filter((entry) => !entry.dir);
    const docxEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".docx"));
    const manifestEntry = entries.find((entry) => entry.name.toLowerCase() === MANIFEST_NAME);
    if (!docxEntry || !manifestEntry) {
        throw new Error("Архив не содержит демаскированный файл и отчёт о результате.");
    }
    const manifest = parseResultManifest(await manifestEntry.async("text"), "unmask_file");
    return {
        docx: await docxEntry.async("blob"),
        docxName: docxEntry.name,
        manifestName: manifestEntry.name,
        manifest,
    };
}
