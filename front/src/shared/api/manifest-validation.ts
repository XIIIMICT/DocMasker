/**
 * Runtime validation for the ZIP-internal `manifest.json` (docs/API.md §manifest.json).
 *
 * The generated OpenAPI DTOs describe HTTP JSON only — this file is a ZIP
 * artifact and has no generated type. A TypeScript assertion never validates
 * untrusted bytes: every field the UI reads is checked here against the exact
 * producer contract (`back/src/masking/ooxml/manifest.py`), and invalid input
 * fails with a fixed, PII-free Russian message before any stat is displayed.
 */

/** The two operations that produce a result manifest (§7). */
export type ManifestOperation = "mask_file" | "unmask_file";

/** Shape accepted after validation; counts the UI reads are guaranteed present. */
export interface ResultManifest {
    schema_version: 1;
    operation: ManifestOperation;
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

/** Stable, machine-readable reasons a result manifest is rejected. */
export type ManifestErrorCode =
    | "manifest_malformed"
    | "manifest_version_unsupported"
    | "manifest_invalid";

/** Fixed Russian details: no input bytes, values, filenames or PII are echoed. */
const ERROR_DETAILS: Record<ManifestErrorCode, string> = {
    manifest_malformed: "Отчёт о результате повреждён.",
    manifest_version_unsupported: "Неподдерживаемая версия отчёта о результате.",
    manifest_invalid: "Некорректный отчёт о результате.",
};

export class ManifestValidationError extends Error {
    readonly code: ManifestErrorCode;

    constructor(code: ManifestErrorCode) {
        super(ERROR_DETAILS[code]);
        this.name = "ManifestValidationError";
        this.code = code;
    }
}

const MANIFEST_SCHEMA_VERSION = 1;

function invalid(): never {
    throw new ManifestValidationError("manifest_invalid");
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function record(value: unknown): Record<string, unknown> {
    if (!isRecord(value)) invalid();
    return value;
}

function stringValue(value: unknown): string {
    if (typeof value !== "string") invalid();
    return value;
}

function flag(value: unknown): boolean {
    if (typeof value !== "boolean") invalid();
    return value;
}

/** Non-negative, exactly representable integer — rejects negatives, NaN, Infinity and huge values. */
function count(value: unknown): number {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) invalid();
    return value;
}

function stringArray(value: unknown): string[] {
    if (!Array.isArray(value)) invalid();
    return value.map((item) => stringValue(item));
}

function countRecord(value: unknown): Record<string, number> {
    const source = record(value);
    const result: Record<string, number> = {};
    for (const [label, entry] of Object.entries(source)) {
        result[label] = count(entry);
    }
    return result;
}

/**
 * `mask_file` always carries the three occurrence aggregates; `unmask_file`
 * always carries `unused_keys`. The other known fields stay optional and are
 * type-checked whenever present; unknown extra fields are ignored so a newer
 * producer can add metadata without breaking this version of the client.
 */
function parseStatistics(
    value: unknown,
    operation: ManifestOperation,
): ResultManifest["statistics"] {
    const raw = record(value);
    if (operation === "mask_file") {
        return {
            total_occurrences: count(raw.total_occurrences),
            occurrences_by_label: countRecord(raw.occurrences_by_label),
            unique_by_label: countRecord(raw.unique_by_label),
            ...(raw.unused_keys === undefined ? {} : { unused_keys: stringArray(raw.unused_keys) }),
        };
    }
    return {
        unused_keys: stringArray(raw.unused_keys),
        ...(raw.total_occurrences === undefined
            ? {}
            : { total_occurrences: count(raw.total_occurrences) }),
        ...(raw.occurrences_by_label === undefined
            ? {}
            : { occurrences_by_label: countRecord(raw.occurrences_by_label) }),
        ...(raw.unique_by_label === undefined
            ? {}
            : { unique_by_label: countRecord(raw.unique_by_label) }),
    };
}

function parseProcessed(
    value: unknown,
): Array<{ name: string; category: string; text_nodes: number }> {
    if (!Array.isArray(value)) invalid();
    return value.map((item) => {
        const part = record(item);
        return {
            name: stringValue(part.name),
            category: stringValue(part.category),
            text_nodes: count(part.text_nodes),
        };
    });
}

function parseCoverage(value: unknown): ResultManifest["coverage"] {
    const raw = record(value);
    return {
        processed_parts: stringArray(raw.processed_parts),
        ...(raw.processed === undefined ? {} : { processed: parseProcessed(raw.processed) }),
        unprocessed_text_parts: stringArray(raw.unprocessed_text_parts),
        rejected_parts: stringArray(raw.rejected_parts),
        raster_image_ocr: stringValue(raw.raster_image_ocr),
    };
}

/**
 * Parse and fully validate external manifest bytes for the expected operation.
 * Every failure throws `ManifestValidationError` with a stable safe detail.
 */
export function parseResultManifest(text: string, operation: ManifestOperation): ResultManifest {
    let parsed: unknown;
    try {
        parsed = JSON.parse(text);
    } catch {
        throw new ManifestValidationError("manifest_malformed");
    }
    const raw = record(parsed);
    if (raw.schema_version !== MANIFEST_SCHEMA_VERSION) {
        throw new ManifestValidationError("manifest_version_unsupported");
    }
    if (raw.operation !== operation || raw.status !== "succeeded") {
        throw new ManifestValidationError("manifest_invalid");
    }
    const input = record(raw.input);
    const output = record(raw.output);
    const cache = record(raw.cache);
    return {
        schema_version: MANIFEST_SCHEMA_VERSION,
        operation,
        status: "succeeded",
        complete: flag(raw.complete),
        masking_ruleset_version: count(raw.masking_ruleset_version),
        exception_config_digest: stringValue(raw.exception_config_digest),
        input: { sha256: stringValue(input.sha256), size_bytes: count(input.size_bytes) },
        output: {
            document_name: stringValue(output.document_name),
            ...(output.keys_name === undefined ? {} : { keys_name: stringValue(output.keys_name) }),
        },
        statistics: parseStatistics(raw.statistics, operation),
        coverage: parseCoverage(raw.coverage),
        cache: { hit: flag(cache.hit), namespace_version: count(cache.namespace_version) },
    };
}
