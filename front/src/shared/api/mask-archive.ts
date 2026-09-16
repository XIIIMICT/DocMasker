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
