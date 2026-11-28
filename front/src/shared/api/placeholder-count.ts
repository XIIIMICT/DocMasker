import {
    scanBoundedDocxPlaceholders,
    type ScanHooks,
    type ScanLimits,
} from "@/shared/api/bounded-zip-scan";

/**
 * Bounds for the pre-operation scan of an uploaded, masked DOCX.
 *
 * `maxParts` matches the backend's 10,000-entry limit. `maxXmlBytes` is a
 * tighter client-side ceiling than the backend's 10× upload uncompressed
 * ceiling, because this pass only feeds a tooltip. `maxSourceBytes` mirrors the
 * default `UPLOAD_MAX_MB` (25) from `back/src/config.py` / `.env.example` and is
 * checked BEFORE the compressed Blob is read into an ArrayBuffer. A drift guard
 * in `placeholder-count.test.ts` pins both backend defaults.
 *
 * These are usability/resource limits, not a security boundary; the backend
 * validates every package authoritatively (docs/API.md). Both client limits
 * fail closed — the workflow surfaces the existing Russian result-pane error
 * instead of a fabricated count.
 */
export const PLACEHOLDER_SCAN_LIMITS = {
    maxParts: 10_000,
    maxXmlBytes: 64 * 1024 * 1024,
    maxSourceBytes: 25 * 1024 * 1024,
} as const;

export interface PlaceholderScanLimits extends ScanLimits {
    readonly maxSourceBytes?: number;
}

export type { ScanHooks as PlaceholderScanHooks };

const SOURCE_TOO_LARGE = "Файл слишком большой для подсчёта фрагментов.";

/**
 * The count describes the uploaded document and is needed *before* the unmask
 * operation runs (the demask input tooltip). The backend computes occurrence
 * aggregates only for masking, and records only placeholder *presence* while
 * unmasking, so a post-operation manifest cannot replace this scan. Cache the
 * promise per Blob: switching modes or re-selecting the same file must not
 * scan it a second time. A rejected scan is cached for the same reason the
 * JSZip loader caches a failed chunk.
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
 * Compressed-size preflight, then one sequential bounded pass. The ZIP is
 * walked by `bounded-zip-scan` (native streaming inflate) instead of JSZip:
 * JSZip's public API offers no incremental decompressed-byte count, so it
 * cannot enforce the ceiling before materialising an entry. JSZip 3.10.2 stays
 * for result-archive unpacking only (docs/DOCX_SUPPORT.md).
 *
 * `hooks` are a test seam for deterministic chunk boundaries and byte
 * instrumentation; production callers pass none.
 */
export async function scanPlaceholdersInDocx(
    blob: Blob,
    limits: PlaceholderScanLimits = PLACEHOLDER_SCAN_LIMITS,
    hooks: ScanHooks = {},
): Promise<Record<string, number>> {
    const sourceLimit = limits.maxSourceBytes ?? PLACEHOLDER_SCAN_LIMITS.maxSourceBytes;
    if (blob.size > sourceLimit) {
        throw new Error(SOURCE_TOO_LARGE);
    }
    const buffer = await blob.arrayBuffer();
    return scanBoundedDocxPlaceholders(new Uint8Array(buffer), limits, hooks);
}
