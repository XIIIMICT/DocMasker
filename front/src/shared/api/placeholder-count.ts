import { loadJsZip } from "@/shared/api/lazy-zip";

/**
 * Bounds for the pre-operation scan of an uploaded, masked DOCX.
 *
 * `maxParts` matches the backend's DOCX entry limit; `maxXmlBytes` is a tighter
 * client-side ceiling than the backend's uncompressed-package limit because this
 * pass only feeds a tooltip. Both limits fail closed — the workflow surfaces the
 * existing Russian result-pane error instead of a fabricated count.
 */
export const PLACEHOLDER_SCAN_LIMITS = {
    maxParts: 10_000,
    maxXmlBytes: 64 * 1024 * 1024,
} as const;

export interface PlaceholderScanLimits {
    readonly maxParts: number;
    readonly maxXmlBytes: number;
}

const CONTENT_TYPES_ENTRY = "[Content_Types].xml";
const PLACEHOLDER = /\[([A-Z]+)_\d+\]/g;

/**
 * The count describes the uploaded document and is needed *before* the unmask
 * operation runs (the demask input tooltip). The backend computes occurrence
 * aggregates only for masking, and records only placeholder *presence* while
 * unmasking, so a post-operation manifest cannot replace this scan. Cache the
 * promise per Blob: switching modes or re-selecting the same file must not
 * decompress it a second time. A rejected scan is cached for the same reason
 * the JSZip loader caches a failed chunk.
 */
const scans = new WeakMap<Blob, Promise<Record<string, number>>>();

export function countPlaceholdersInDocx(blob: Blob): Promise<Record<string, number>> {
    let pending = scans.get(blob);
    if (pending === undefined) {
        pending = scanPlaceholdersInDocx(blob, PLACEHOLDER_SCAN_LIMITS);
        scans.set(blob, pending);
    }
    return pending;
}

/**
 * One sequential ZIP pass: parts are decompressed one at a time and released
 * before the next one, so peak memory is one XML part plus the archive. The
 * limits are injectable only so the bounds can be tested without a 64 MiB
 * fixture; production callers use the exported defaults.
 */
export async function scanPlaceholdersInDocx(
    blob: Blob,
    limits: PlaceholderScanLimits = PLACEHOLDER_SCAN_LIMITS,
): Promise<Record<string, number>> {
    const JSZip = await loadJsZip();
    const archive = await JSZip.loadAsync(await blob.arrayBuffer());
    const entries = Object.values(archive.files);
    if (entries.length > limits.maxParts) {
        throw new Error("Документ содержит слишком много внутренних частей.");
    }
    const counts: Record<string, number> = {};
    let scannedBytes = 0;
    for (const entry of entries) {
        if (entry.dir) continue;
        const name = entry.name;
        if (!name.toLowerCase().endsWith(".xml") || name === CONTENT_TYPES_ENTRY) continue;
        const xml = await entry.async("string");
        // A supported XML part starts with markup; never regex-scan a binary
        // payload that merely carries an `.xml` name.
        if (!xml.trimStart().startsWith("<")) continue;
        scannedBytes += xml.length;
        if (scannedBytes > limits.maxXmlBytes) {
            throw new Error("Документ слишком большой для подсчёта фрагментов.");
        }
        for (const match of xml.matchAll(PLACEHOLDER)) {
            const label = match[1];
            if (label === undefined) continue;
            const key = label.toLowerCase();
            counts[key] = (counts[key] ?? 0) + 1;
        }
    }
    return counts;
}
