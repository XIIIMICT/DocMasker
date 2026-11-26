import type { DocxResult, MaskedFileResult } from "@/features/masking/types";

/** Trigger a browser download without ever mutating the document Blob. */
export function downloadBlob(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    anchor.click();
    URL.revokeObjectURL(url);
}

export function downloadMaskedFile(maskedFile: MaskedFileResult | null): void {
    if (!maskedFile) return;
    downloadBlob(maskedFile.result, maskedFile.resultName);
}

export function downloadMaskedText(result: string, textBaseName: string | null): void {
    if (result.length === 0) return;
    const base = textBaseName ?? "текст";
    downloadBlob(
        new Blob([result], { type: "text/plain;charset=utf-8" }),
        `${base} (замаскированный).txt`,
    );
}

/** Keys download: in file mode the name comes from the archive, else derived. */
export function downloadKeys(
    keys: string,
    maskedFile: MaskedFileResult | null,
    textBaseName: string | null,
): void {
    if (keys.length === 0) return;
    if (maskedFile) {
        downloadBlob(maskedFile.keys, maskedFile.keysName);
        return;
    }
    const base = textBaseName ?? "текст";
    downloadBlob(
        new Blob([keys], { type: "application/yaml;charset=utf-8" }),
        `${base} (ключи демаскировки).yaml`,
    );
}

/** Download the restored (demasked) artifact, text or document. */
export function downloadRestored(
    restored: string,
    restoredFile: DocxResult | null,
    textBaseName: string | null,
): void {
    if (restoredFile) {
        downloadBlob(restoredFile.blob, restoredFile.name);
        return;
    }
    if (restored.length === 0) return;
    const base = textBaseName ?? "текст";
    downloadBlob(
        new Blob([restored], { type: "text/plain;charset=utf-8" }),
        `${base} (демаскированный).txt`,
    );
}
