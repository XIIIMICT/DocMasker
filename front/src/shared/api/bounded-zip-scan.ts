/**
 * Narrow, read-only ZIP walker used **only** by the pre-operation placeholder
 * scan. It reads the central directory and one local header at a time, never
 * materialises an XML entry, and feeds the browser's streaming inflater into a
 * byte counter so the hard ceiling is enforced *while* inflating.
 *
 * This is a resource-usability layer, not a security boundary: ZIP metadata is
 * untrusted, the backend validates every uploaded package authoritatively, and
 * marker counts only feed a tooltip (docs/API.md, docs/DOCX_SUPPORT.md).
 */

/** Decompressed bytes accepted from one entry before the ceiling terminates it. */
export interface ScanLimits {
    readonly maxParts: number;
    readonly maxXmlBytes: number;
}

/**
 * Test seam: supply deterministic chunks or observe exactly which bytes reach
 * the UTF-8 decoder. Production uses `nativeDeflateRaw` and no observer.
 */
export interface ScanHooks {
    readonly decompress?: EntryDecompressor;
    readonly onDecodedBytes?: (bytes: number) => void;
}

export type EntryDecompressor = (
    compressed: Uint8Array,
    method: number,
) => ReadableStream<Uint8Array>;

interface EntryMeta {
    readonly name: string;
    readonly method: number;
    readonly compressedSize: number;
    readonly uncompressedSize: number;
    readonly localOffset: number;
}

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const MAX_ZIP_COMMENT_BYTES = 0xffff;
const ZIP64_MARKER = 0xffffffff;
const FLAG_ENCRYPTED = 0x0001;
const FLAG_STRONG_ENCRYPTION = 0x0040;
const METHOD_STORED = 0;
const METHOD_DEFLATE = 8;
const CONTENT_TYPES_ENTRY = "[Content_Types].xml";
const PLACEHOLDER = /\[([A-Z]+)_\d+\]/g;
/** Bounded match carry: far above any configured label/counter length. */
const MAX_CARRY_CHARS = 512;
/** Stored entries are views over the same buffer, never a copied full entry. */
const STORED_SLICE_BYTES = 64 * 1024;

const MALFORMED = "Документ не является корректным DOCX-архивом.";
const UNSUPPORTED = "Документ использует неподдерживаемое сжатие или шифрование.";
const TOO_MANY_PARTS = "Документ содержит слишком много внутренних частей.";
const TOO_LARGE = "Документ слишком большой для подсчёта фрагментов.";

function u16(view: DataView, offset: number): number {
    return view.getUint16(offset, true);
}

function u32(view: DataView, offset: number): number {
    return view.getUint32(offset, true);
}

function requireRange(offset: number, length: number, total: number): void {
    if (offset < 0 || length < 0 || offset > total - length) {
        throw new Error(MALFORMED);
    }
}

function isEligibleXml(name: string): boolean {
    return name.toLowerCase().endsWith(".xml") && name !== CONTENT_TYPES_ENTRY;
}

function findEocd(view: DataView): number {
    const end = view.byteLength;
    if (end < 22) throw new Error(MALFORMED);
    const lowest = Math.max(0, end - 22 - MAX_ZIP_COMMENT_BYTES);
    for (let at = end - 22; at >= lowest; at -= 1) {
        if (u32(view, at) === EOCD_SIGNATURE) return at;
    }
    throw new Error(MALFORMED);
}

/**
 * Parse the central directory only. Declared sizes are used for early
 * rejection (defence in depth) and to locate compressed bytes; they are never
 * treated as proof of a decompression bound — that is enforced during inflate.
 */
function readEntries(bytes: Uint8Array, limits: ScanLimits): EntryMeta[] {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const eocd = findEocd(view);
    const diskEntries = u16(view, eocd + 8);
    const totalEntries = u16(view, eocd + 10);
    if (
        diskEntries !== totalEntries ||
        totalEntries === 0xffff ||
        u32(view, eocd + 12) === ZIP64_MARKER ||
        u32(view, eocd + 16) === ZIP64_MARKER
    ) {
        throw new Error(UNSUPPORTED);
    }
    if (totalEntries > limits.maxParts) throw new Error(TOO_MANY_PARTS);

    const cdSize = u32(view, eocd + 12);
    const cdOffset = u32(view, eocd + 16);
    requireRange(cdOffset, cdSize, eocd);
    const cdEnd = cdOffset + cdSize;
    const names = new TextDecoder();
    const entries: EntryMeta[] = [];
    let cursor = cdOffset;
    let declaredXmlBytes = 0;

    for (let index = 0; index < totalEntries; index += 1) {
        requireRange(cursor, 46, cdEnd);
        if (u32(view, cursor) !== CENTRAL_SIGNATURE) throw new Error(MALFORMED);
        const flags = u16(view, cursor + 8);
        if ((flags & (FLAG_ENCRYPTED | FLAG_STRONG_ENCRYPTION)) !== 0) {
            throw new Error(UNSUPPORTED);
        }
        const method = u16(view, cursor + 10);
        const compressedSize = u32(view, cursor + 20);
        const uncompressedSize = u32(view, cursor + 24);
        if (
            compressedSize === ZIP64_MARKER ||
            uncompressedSize === ZIP64_MARKER ||
            (method !== METHOD_STORED && method !== METHOD_DEFLATE)
        ) {
            throw new Error(UNSUPPORTED);
        }
        const nameLength = u16(view, cursor + 28);
        const extraLength = u16(view, cursor + 30);
        const commentLength = u16(view, cursor + 32);
        const localOffset = u32(view, cursor + 42);
        requireRange(cursor + 46, nameLength + extraLength + commentLength, cdEnd);
        const name = names.decode(bytes.subarray(cursor + 46, cursor + 46 + nameLength));

        if (isEligibleXml(name)) {
            // Untrusted metadata: an early rejection only, never the bound.
            if (uncompressedSize > limits.maxXmlBytes) throw new Error(TOO_LARGE);
            declaredXmlBytes += uncompressedSize;
            if (declaredXmlBytes > limits.maxXmlBytes) throw new Error(TOO_LARGE);
        }
        entries.push({ name, method, compressedSize, uncompressedSize, localOffset });
        cursor += 46 + nameLength + extraLength + commentLength;
    }
    if (cursor > cdEnd) throw new Error(MALFORMED);
    return entries;
}

function oneChunk(bytes: Uint8Array): ReadableStream<Uint8Array> {
    return new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(bytes);
            controller.close();
        },
    });
}

function storedSlices(bytes: Uint8Array): ReadableStream<Uint8Array> {
    return new ReadableStream<Uint8Array>({
        start(controller) {
            for (let at = 0; at < bytes.byteLength; at += STORED_SLICE_BYTES) {
                controller.enqueue(bytes.subarray(at, at + STORED_SLICE_BYTES));
            }
            controller.close();
        },
    });
}

/** Browser-native streaming inflate: no runtime dependency, no full entry. */
export function nativeDeflateRaw(
    compressed: Uint8Array,
    method: number,
): ReadableStream<Uint8Array> {
    if (method === METHOD_STORED) return storedSlices(compressed);
    if (method !== METHOD_DEFLATE || typeof DecompressionStream === "undefined") {
        throw new Error(UNSUPPORTED);
    }
    // The DOM lib types the writable side as BufferSource (wider than the
    // Uint8Array chunks this reader emits); the runtime accepts Uint8Array.
    const inflate = new DecompressionStream("deflate-raw") as unknown as ReadableWritablePair<
        Uint8Array,
        Uint8Array
    >;
    return oneChunk(compressed).pipeThrough(inflate);
}

function firstNonWhitespace(text: string): number {
    return text.search(/\S/);
}

/**
 * Count matches with the same left-to-right, non-overlapping meaning as the
 * original whole-string regex. Only matches that *finish* in the new text are
 * counted, so a match spanning the carry is counted once; the carry is capped
 * so no decompressed string is retained across many chunks.
 */
function makeMarkerScanner(counts: Record<string, number>) {
    let carry = "";
    return function consume(text: string): void {
        const combined = carry + text;
        PLACEHOLDER.lastIndex = 0;
        for (const match of combined.matchAll(PLACEHOLDER)) {
            const end = (match.index ?? 0) + match[0].length;
            if (end <= carry.length) continue; // already counted in a prior chunk
            const label = match[1];
            if (label === undefined) continue;
            const key = label.toLowerCase();
            counts[key] = (counts[key] ?? 0) + 1;
        }
        carry = combined.length > MAX_CARRY_CHARS ? combined.slice(-MAX_CARRY_CHARS) : combined;
    };
}

async function scanEntry(
    bytes: Uint8Array,
    entry: EntryMeta,
    state: { scannedBytes: number; maxBytes: number },
    hooks: ScanHooks,
    consume: (text: string) => void,
): Promise<void> {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    requireRange(entry.localOffset, 30, bytes.byteLength);
    if (u32(view, entry.localOffset) !== LOCAL_SIGNATURE) throw new Error(MALFORMED);
    const nameLength = u16(view, entry.localOffset + 26);
    const extraLength = u16(view, entry.localOffset + 28);
    const dataStart = entry.localOffset + 30 + nameLength + extraLength;
    requireRange(dataStart, entry.compressedSize, bytes.byteLength);
    const compressed = bytes.subarray(dataStart, dataStart + entry.compressedSize);

    const decompress = hooks.decompress ?? nativeDeflateRaw;
    const reader = decompress(compressed, entry.method).getReader();
    const decoder = new TextDecoder("utf-8");
    let markupStarted = false;

    try {
        for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            if (value.byteLength === 0) continue;
            // Hard ceiling: a crossing chunk is discarded, never decoded.
            if (state.scannedBytes + value.byteLength > state.maxBytes) {
                throw new Error(TOO_LARGE);
            }
            state.scannedBytes += value.byteLength;
            const text = decoder.decode(value, { stream: true });
            hooks.onDecodedBytes?.(value.byteLength);
            if (text.length === 0) continue;
            if (!markupStarted) {
                const at = firstNonWhitespace(text);
                if (at < 0) continue;
                // Skip `.xml`-named binary payloads, matching prior semantics.
                if (text.charCodeAt(at) !== 60) break;
                markupStarted = true;
                consume(text.slice(at));
            } else {
                consume(text);
            }
        }
        // Flush a UTF-8 sequence split across the final chunk boundary.
        const tail = decoder.decode();
        if (tail.length > 0 && markupStarted) consume(tail);
    } finally {
        try {
            await reader.cancel();
        } catch {
            // The stream may already be closed by an inflate error.
        }
    }
}

/**
 * Sequential, bounded pass. At most one entry's compressed view and one
 * decompressed chunk are live at a time; only counts and a capped carry stay
 * between entries.
 */
export async function scanBoundedDocxPlaceholders(
    bytes: Uint8Array,
    limits: ScanLimits,
    hooks: ScanHooks = {},
): Promise<Record<string, number>> {
    const entries = readEntries(bytes, limits);
    const counts: Record<string, number> = {};
    const state = { scannedBytes: 0, maxBytes: limits.maxXmlBytes };
    for (const entry of entries) {
        // Directory records end with "/" and cannot be eligible XML parts.
        if (!isEligibleXml(entry.name)) continue;
        await scanEntry(bytes, entry, state, hooks, makeMarkerScanner(counts));
    }
    return counts;
}
