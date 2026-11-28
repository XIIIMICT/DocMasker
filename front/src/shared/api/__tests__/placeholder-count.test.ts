import { describe, expect, it, spyOn } from "bun:test";
import { readFileSync } from "node:fs";

import JSZip from "jszip";

import type { EntryDecompressor } from "@/shared/api/bounded-zip-scan";
import {
    countPlaceholdersInDocx,
    PLACEHOLDER_SCAN_LIMITS,
    scanPlaceholdersInDocx,
    type PlaceholderScanHooks,
} from "@/shared/api/placeholder-count";

type Compression = "DEFLATE" | "STORE";

async function zipBlob(
    files: Record<string, string>,
    compression: Compression = "DEFLATE",
): Promise<Blob> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(files)) {
        zip.file(name, content, { createFolders: false });
    }
    return new Blob([await zip.generateAsync({ type: "arraybuffer", compression })], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
}

/** Rewrite every central-directory uncompressed size (untrusted metadata). */
async function patchDeclaredSizes(blob: Blob, value: number): Promise<Blob> {
    const buffer = await blob.arrayBuffer();
    const view = new DataView(buffer);
    for (let at = 0; at < buffer.byteLength - 46; at += 1) {
        if (view.getUint32(at, true) === 0x02014b50) view.setUint32(at + 24, value, true);
    }
    return new Blob([buffer]);
}

/** Deterministic inflate chunks. STORE entries make `compressed` the raw XML. */
function chunked(size: number, pulled?: number[]): EntryDecompressor {
    return (compressed) => {
        let offset = 0;
        // highWaterMark 0: a chunk is emitted only when the scanner reads it,
        // so `pulled` is an exact record of bytes delivered to the scanner.
        return new ReadableStream<Uint8Array>(
            {
                pull(controller) {
                    if (offset >= compressed.byteLength) {
                        controller.close();
                        return;
                    }
                    const chunk = compressed.subarray(offset, offset + size);
                    offset += chunk.byteLength;
                    pulled?.push(chunk.byteLength);
                    controller.enqueue(chunk);
                },
            },
            { highWaterMark: 0 },
        );
    };
}

function byteCounter() {
    let total = 0;
    return {
        hooks: {
            onDecodedBytes: (bytes: number) => {
                total += bytes;
            },
        } satisfies PlaceholderScanHooks,
        get total(): number {
            return total;
        },
    };
}

const MASKED_DOCX = {
    "[Content_Types].xml": "<Types/>",
    "word/document.xml": "<w:t>[PERSON_1] и [PERSON_1], [PHONE_1]</w:t>",
    "word/header1.xml": "<w:t>ООО [ORGANIZATION_1]</w:t>",
    "word/media/image.png": "[PERSON_9]", // binary part, not XML
};

describe("countPlaceholdersInDocx", () => {
    it("counts placeholders across all XML parts by label", async () => {
        const blob = await zipBlob(MASKED_DOCX);
        expect(await countPlaceholdersInDocx(blob)).toEqual({
            person: 2,
            phone: 1,
            organization: 1,
        });
    });

    it("returns an empty map when nothing was masked", async () => {
        const blob = await zipBlob({ "word/document.xml": "<w:t>Чистый текст</w:t>" });
        expect(await countPlaceholdersInDocx(blob)).toEqual({});
    });

    it("keeps raw-XML meaning without inferring from keys, filenames or binary payloads", async () => {
        const blob = await zipBlob({
            "word/document.xml": "<w:t>[CUSTOM_7]</w:t>",
            // Declared as XML but not markup: must not be regex-scanned.
            "customXml/item1.xml": "[PERSON_41]",
            "word/styles.xml": '<w:style w:styleId="[PHONE_8]"/>',
        });
        expect(await countPlaceholdersInDocx(blob)).toEqual({
            custom: 1,
            // An attribute value is still a literal placeholder in an XML part:
            // the existing raw-XML meaning is preserved, not narrowed by keys.
            phone: 1,
        });
    });

    it("reuses one scan for concurrent calls with the same Blob", async () => {
        const blob = await zipBlob(MASKED_DOCX);
        const arrayBuffer = spyOn(blob, "arrayBuffer");
        try {
            const [first, second] = await Promise.all([
                countPlaceholdersInDocx(blob),
                countPlaceholdersInDocx(blob),
            ]);
            expect(first).toEqual({ person: 2, phone: 1, organization: 1 });
            expect(second).toBe(first);
            expect(arrayBuffer).toHaveBeenCalledTimes(1);
            expect(await countPlaceholdersInDocx(blob)).toBe(first);
            expect(arrayBuffer).toHaveBeenCalledTimes(1);
        } finally {
            arrayBuffer.mockRestore();
        }
    });

    it("rejects a malformed archive instead of returning an empty count", async () => {
        await expect(countPlaceholdersInDocx(new Blob(["not a zip"]))).rejects.toThrow();
    });

    it("scans distinct Blobs independently", async () => {
        const first = await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" });
        const second = await zipBlob({ "word/document.xml": "<w:t>[PHONE_1]</w:t>" });
        expect(await countPlaceholdersInDocx(first)).toEqual({ person: 1 });
        expect(await countPlaceholdersInDocx(second)).toEqual({ phone: 1 });
    });
});

describe("scanPlaceholdersInDocx limits", () => {
    it("pins the client ceilings to the backend defaults (drift guard)", () => {
        const config = readFileSync(
            new URL("../../../../../back/src/config.py", import.meta.url),
            "utf8",
        );
        const validation = readFileSync(
            new URL("../../../../../back/src/masking/docx_validation.py", import.meta.url),
            "utf8",
        );
        const exampleEnv = readFileSync(
            new URL("../../../../../.env.example", import.meta.url),
            "utf8",
        );
        expect(config).toMatch(/max_upload_mb:\s*int\s*=\s*Field\(default=25/);
        expect(exampleEnv).toMatch(/^UPLOAD_MAX_MB=25$/m);
        expect(validation).toMatch(/_ENTRY_COUNT_LIMIT = 10_000/);
        expect(PLACEHOLDER_SCAN_LIMITS.maxSourceBytes).toBe(25 * 1024 * 1024);
        expect(PLACEHOLDER_SCAN_LIMITS.maxParts).toBe(10_000);
    });

    it("refuses to decompress more than the allowed number of parts", async () => {
        const blob = await zipBlob({
            "word/document.xml": "<w:t>[PERSON_1]</w:t>",
            "word/footnotes.xml": "<w:t>[PHONE_1]</w:t>",
        });
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 1, maxXmlBytes: 1024 }),
        ).rejects.toThrow("слишком много внутренних частей");
    });

    it("accepts exactly the entry-count limit", async () => {
        const blob = await zipBlob({
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:t>[PERSON_1]</w:t>",
        });
        expect(await scanPlaceholdersInDocx(blob, { maxParts: 2, maxXmlBytes: 1024 })).toEqual({
            person: 1,
        });
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 1, maxXmlBytes: 1024 }),
        ).rejects.toThrow("слишком много внутренних частей");
    });

    it("refuses a part whose decompressed XML exceeds the byte ceiling", async () => {
        const blob = await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" });
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: 4 }),
        ).rejects.toThrow("слишком большой для подсчёта");
    });

    it("accepts one part exactly at the byte ceiling and rejects one byte above", async () => {
        const xml = "<w:t>[PERSON_1]</w:t>";
        const bytes = new TextEncoder().encode(xml).byteLength;
        const blob = await zipBlob({ "word/document.xml": xml }, "STORE");
        const counter = byteCounter();
        expect(
            await scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: bytes }, counter.hooks),
        ).toEqual({ person: 1 });
        expect(counter.total).toBe(bytes);
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: bytes - 1 }),
        ).rejects.toThrow("слишком большой для подсчёта");
    });

    it("accounts aggregate decompressed bytes across XML parts", async () => {
        const first = "<w:t>[PERSON_1]</w:t>"; // 19 ASCII bytes
        const second = "<w:t>[PHONE_1]</w:t>"; // 19 ASCII bytes
        const total = new TextEncoder().encode(first + second).byteLength;
        const blob = await zipBlob(
            { "word/document.xml": first, "word/footnotes.xml": second },
            "STORE",
        );
        expect(await scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: total })).toEqual({
            person: 1,
            phone: 1,
        });
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: total - 1 }),
        ).rejects.toThrow("слишком большой для подсчёта");
    });

    it("counts UTF-8 bytes instead of UTF-16 code units", async () => {
        const xml = `<w:t>${"Привет".repeat(20)} [PERSON_1]</w:t>`;
        const units = xml.length;
        const bytes = new TextEncoder().encode(xml).byteLength;
        expect(bytes).toBeGreaterThan(units);
        const blob = await zipBlob({ "word/document.xml": xml }, "STORE");
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: units }),
        ).rejects.toThrow("слишком большой для подсчёта");
        expect(await scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: bytes })).toEqual({
            person: 1,
        });
    });

    it("rejects a compressed Blob before reading the whole ArrayBuffer", async () => {
        const blob = await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" });
        const arrayBuffer = spyOn(blob, "arrayBuffer");
        try {
            await expect(
                scanPlaceholdersInDocx(blob, {
                    maxParts: 10,
                    maxXmlBytes: 1024,
                    maxSourceBytes: 1,
                }),
            ).rejects.toThrow("Файл слишком большой для подсчёта");
            expect(arrayBuffer).not.toHaveBeenCalled();
        } finally {
            arrayBuffer.mockRestore();
        }
    });

    it("rejects hostile declared uncompressed sizes before any inflate", async () => {
        const blob = await patchDeclaredSizes(
            await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" }, "STORE"),
            100 * 1024 * 1024,
        );
        const counter = byteCounter();
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: 1024 }, counter.hooks),
        ).rejects.toThrow("слишком большой для подсчёта");
        expect(counter.total).toBe(0);
    });

    it("rejects an encrypted/hostile ZIP flag before inflating", async () => {
        const buffer = await (
            await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" })
        ).arrayBuffer();
        const view = new DataView(buffer);
        for (let at = 0; at < buffer.byteLength - 46; at += 1) {
            if (view.getUint32(at, true) === 0x02014b50) view.setUint16(at + 8, 0x0001, true);
        }
        await expect(
            scanPlaceholdersInDocx(new Blob([buffer]), { maxParts: 10, maxXmlBytes: 1024 }),
        ).rejects.toThrow("шифрование");
    });

    it("rejects a truncated central directory", async () => {
        const buffer = await (
            await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" })
        ).arrayBuffer();
        await expect(
            scanPlaceholdersInDocx(new Blob([new Uint8Array(buffer, 0, 40)]), {
                maxParts: 10,
                maxXmlBytes: 1024,
            }),
        ).rejects.toThrow();
    });
});

describe("scanPlaceholdersInDocx streaming semantics", () => {
    it("matches a marker split across several decompression chunks", async () => {
        const blob = await zipBlob(
            { "word/document.xml": "<w:t>[PERSON_123] и [PHONE_1]</w:t>" },
            "STORE",
        );
        expect(
            await scanPlaceholdersInDocx(
                blob,
                { maxParts: 10, maxXmlBytes: 1024 },
                { decompress: chunked(3) },
            ),
        ).toEqual({ person: 1, phone: 1 });
    });

    it("counts adjacent and repeated markers at chunk boundaries exactly once", async () => {
        const xml = "<w:t>[PERSON_1][PERSON_1][PHONE_1]x[PERSON_1]</w:t>";
        const blob = await zipBlob({ "word/document.xml": xml }, "STORE");
        const pulled: number[] = [];
        const counts = await scanPlaceholdersInDocx(
            blob,
            { maxParts: 10, maxXmlBytes: 1024 },
            { decompress: chunked(10, pulled) },
        );
        expect(counts).toEqual({ person: 3, phone: 1 });
        expect(pulled.length).toBeGreaterThan(2);
    });

    it("does not count malformed marker-shaped prefixes", async () => {
        const xml = "<w:t>[PERSON_ [PERSON_1X] [person_1] [PERSON_12]!</w:t>";
        const blob = await zipBlob({ "word/document.xml": xml }, "STORE");
        expect(
            await scanPlaceholdersInDocx(
                blob,
                { maxParts: 10, maxXmlBytes: 1024 },
                { decompress: chunked(4) },
            ),
        ).toEqual({ person: 1 });
    });

    it("decodes UTF-8 correctly when a multibyte character is split across chunks", async () => {
        const xml = `<w:t>${"Привет мир".repeat(10)} [ORGANIZATION_1]</w:t>`;
        const blob = await zipBlob({ "word/document.xml": xml }, "STORE");
        // Chunk size 3 splits the two-byte Cyrillic sequences repeatedly.
        expect(
            await scanPlaceholdersInDocx(
                blob,
                { maxParts: 10, maxXmlBytes: 4096 },
                { decompress: chunked(3) },
            ),
        ).toEqual({ organization: 1 });
    });

    it("stops inflating at the hard ceiling even with understated metadata", async () => {
        const ceiling = 1000;
        const xml = "<w:t>" + "x".repeat(20_000) + "[PERSON_1]</w:t>";
        const blob = await patchDeclaredSizes(
            await zipBlob({ "word/document.xml": xml }, "STORE"),
            10,
        );
        const pulled: number[] = [];
        const counter = byteCounter();
        await expect(
            scanPlaceholdersInDocx(
                blob,
                { maxParts: 10, maxXmlBytes: ceiling },
                { ...counter.hooks, decompress: chunked(8, pulled) },
            ),
        ).rejects.toThrow("слишком большой для подсчёта");
        // Exactly `ceiling` bytes reached the decoder; the crossing chunk was
        // pulled from the stream but discarded before decoding or retaining.
        expect(counter.total).toBe(ceiling);
        expect(pulled.reduce((sum, bytes) => sum + bytes, 0)).toBe(ceiling + 8);
    });

    it("bounds a STORED entry through the production slice path", async () => {
        const xml = "<w:t>" + "x".repeat(200_000) + "</w:t>";
        const blob = await patchDeclaredSizes(
            await zipBlob({ "word/document.xml": xml }, "STORE"),
            10,
        );
        const counter = byteCounter();
        await expect(
            scanPlaceholdersInDocx(blob, { maxParts: 10, maxXmlBytes: 100_000 }, counter.hooks),
        ).rejects.toThrow("слишком большой для подсчёта");
        // One 64 KiB slice accepted, the next discarded before decoding.
        expect(counter.total).toBe(64 * 1024);
    });

    it("surfaces genuine inflate errors instead of a fabricated zero count", async () => {
        const blob = await zipBlob({ "word/document.xml": "<w:t>[PERSON_1]</w:t>" }, "STORE");
        const broken: EntryDecompressor = () =>
            new ReadableStream<Uint8Array>({
                start(controller) {
                    controller.error(new Error("inflate failed"));
                },
            });
        await expect(
            scanPlaceholdersInDocx(
                blob,
                { maxParts: 10, maxXmlBytes: 1024 },
                { decompress: broken },
            ),
        ).rejects.toThrow("inflate failed");
    });
});
