import { describe, expect, it, spyOn } from "bun:test";

import JSZip from "jszip";

import { countPlaceholdersInDocx, scanPlaceholdersInDocx } from "@/shared/api/placeholder-count";

async function zipBlob(files: Record<string, string>): Promise<Blob> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(files)) zip.file(name, content);
    return new Blob([await zip.generateAsync({ type: "arraybuffer" })], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
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
});

describe("scanPlaceholdersInDocx limits", () => {
    it("refuses to decompress more than the allowed number of parts", async () => {
        const blob = await zipBlob({
            "word/document.xml": "<w:t>[PERSON_1]</w:t>",
            "word/footnotes.xml": "<w:t>[PHONE_1]</w:t>",
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
});
