import type { DocxResult, MaskedFileResult } from "@/features/masking/types";

/** Trigger a browser download without ever mutating the document Blob. */
export function downloadBlob(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    try {
        anchor.click();
    } finally {
        anchor.remove();
        // `click()` only queues the download: the browser starts reading the
        // Blob URL in a later task, so a synchronous revoke can abort it
        // (notably Safari, and large files). A zero-delay timer moves the
        // revoke to the next macrotask — the earliest point after the click
        // has been handed off — instead of a magic millisecond delay. A
        // microtask would still run before the download task, so it is not
        // enough. `finally` guarantees cleanup even if `click()` throws; the
        // error itself is not caught.
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
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
