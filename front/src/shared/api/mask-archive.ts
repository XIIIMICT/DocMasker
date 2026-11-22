import JSZip from "jszip";

/** The two artifacts produced by one masking run. */
export interface MaskedBundle {
    /** The masked document (`.docx`) or text (`.txt`). */
    result: Blob;
    resultName: string;
    /** The demasking keys (`.yaml`). */
    keys: Blob;
    keysName: string;
}

/**
 * Unpack the ZIP returned by `/api/mask-file` into the masked document and its
 * demasking keys.
 *
 * The backend bundles both because a keys file is meaningless without its
 * document; the user still downloads them as two separate files, never the
 * archive. Names are taken from the archive entries themselves.
 */
export async function unpackMaskArchive(archive: Blob): Promise<MaskedBundle> {
    const zip = await JSZip.loadAsync(archive);
    const entries = Object.values(zip.files).filter((entry) => !entry.dir);
    const resultEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".docx"));
    const keysEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".yaml"));
    if (!resultEntry || !keysEntry) {
        throw new Error("Архив не содержит замаскированный файл и ключи демаскировки.");
    }
    return {
        result: await resultEntry.async("blob"),
        resultName: resultEntry.name,
        keys: await keysEntry.async("blob"),
        keysName: keysEntry.name,
    };
}

/** The restored document plus the manifest of unapplied keys (F-05: in-body, not headers). */
export interface RestoredBundle {
    docx: Blob;
    docxName: string;
    manifestName: string;
    manifest: { unused_keys: string[] };
}

/**
 * Unpack the ZIP returned by `/api/unmask-file` into the restored document and
 * its demasking report. The manifest (unused keys) travels in the body since
 * the old `X-Unused-Keys` header leaked PII into transport metadata and could
 * outgrow header size limits.
 */
export async function unpackRestoreArchive(archive: Blob): Promise<RestoredBundle> {
    const zip = await JSZip.loadAsync(archive);
    const entries = Object.values(zip.files).filter((entry) => !entry.dir);
    const docxEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".docx"));
    const manifestEntry = entries.find((entry) => entry.name.toLowerCase().endsWith(".json"));
    if (!docxEntry || !manifestEntry) {
        throw new Error("Архив не содержит демаскированный файл и отчёт о демаскировке.");
    }
    const manifestText = await manifestEntry.async("text");
    let manifest: RestoredBundle["manifest"];
    try {
        const parsed = JSON.parse(manifestText) as RestoredBundle["manifest"];
        manifest = { unused_keys: Array.isArray(parsed.unused_keys) ? parsed.unused_keys : [] };
    } catch {
        throw new Error("Отчёт о демаскировке повреждён.");
    }
    return {
        docx: await docxEntry.async("blob"),
        docxName: docxEntry.name,
        manifestName: manifestEntry.name,
        manifest,
    };
}
