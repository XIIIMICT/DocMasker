import { loadJsZip } from "@/shared/api/lazy-zip";

/** Versioned result manifest shared by sync and job file operations (§7). */
export interface ResultManifest {
    schema_version: number;
    operation: "mask_file" | "unmask_file";
    status: "succeeded";
    complete: boolean;
    masking_ruleset_version: number;
    exception_config_digest: string;
    input: { sha256: string; size_bytes: number };
    output: { document_name: string; keys_name?: string };
    statistics: {
        total_occurrences?: number;
        occurrences_by_label?: Record<string, number>;
        unique_by_label?: Record<string, number>;
        unused_keys?: string[];
    };
    coverage: {
        processed_parts: string[];
        processed?: Array<{ name: string; category: string; text_nodes: number }>;
        unprocessed_text_parts: string[];
        rejected_parts: string[];
        raster_image_ocr: string;
    };
    cache: { hit: boolean; namespace_version: number };
}

export const MANIFEST_NAME = "manifest.json";
const MANIFEST_SCHEMA_VERSION = 1;

/** Require and validate manifest.json (§7.3: never silently treat as zero). */
function readManifest(text: string, operation: ResultManifest["operation"]): ResultManifest {
    let parsed: unknown;
    try {
        parsed = JSON.parse(text);
    } catch {
        throw new Error("Отчёт о результате повреждён.");
    }
    const manifest = parsed as ResultManifest;
    if (
        !manifest ||
        manifest.schema_version !== MANIFEST_SCHEMA_VERSION ||
        manifest.operation !== operation ||
        manifest.status !== "succeeded" ||
        !manifest.statistics ||
        !manifest.coverage
    ) {
        throw new Error("Неподдерживаемая версия отчёта о результате.");
    }
    return manifest;
}

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

export function validateManifestText(
    text: string,
    operation: ResultManifest["operation"],
): ResultManifest {
    return readManifest(text, operation);
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
    const manifest = readManifest(await manifestEntry.async("text"), "mask_file");
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
    const manifest = readManifest(await manifestEntry.async("text"), "unmask_file");
    return {
        docx: await docxEntry.async("blob"),
        docxName: docxEntry.name,
        manifestName: manifestEntry.name,
        manifest,
    };
}
