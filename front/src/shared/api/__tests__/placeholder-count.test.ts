import { describe, expect, it } from "bun:test";

import JSZip from "jszip";

import { countPlaceholdersInDocx } from "@/shared/api/placeholder-count";

async function zipBlob(files: Record<string, string>): Promise<Blob> {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(files)) zip.file(name, content);
    return new Blob([await zip.generateAsync({ type: "arraybuffer" })], {
        type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
}

describe("countPlaceholdersInDocx", () => {
    it("counts placeholders across all XML parts by label", async () => {
        const blob = await zipBlob({
            "[Content_Types].xml": "<Types/>",
            "word/document.xml": "<w:t>[PERSON_1] и [PERSON_1], [PHONE_1]</w:t>",
            "word/header1.xml": "<w:t>ООО [ORGANIZATION_1]</w:t>",
            "word/media/image.png": "[PERSON_9]", // binary part, not XML
        });
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
});
