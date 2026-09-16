import { describe, expect, it } from "bun:test";

import JSZip from "jszip";

import { unpackMaskArchive } from "@/shared/api/mask-archive";

async function makeArchive(entries: Record<string, string>): Promise<Blob> {
    const zip = new JSZip();
    for (const [name, contents] of Object.entries(entries)) {
        zip.file(name, contents);
    }
    return zip.generateAsync({ type: "blob" });
}

describe("unpackMaskArchive", () => {
    it("returns the masked document and its keys with the archive names", async () => {
        const archive = await makeArchive({
            "доклад (замаскированный).docx": "docx-bytes",
            "доклад (ключи демаскировки).yaml": "Физлица:\n  - Иван Петров",
        });

        const bundle = await unpackMaskArchive(archive);

        expect(bundle.resultName).toBe("доклад (замаскированный).docx");
        expect(bundle.keysName).toBe("доклад (ключи демаскировки).yaml");
        expect(await bundle.result.text()).toBe("docx-bytes");
        expect(await bundle.keys.text()).toContain("Иван Петров");
    });

    it("throws when a half is missing", async () => {
        const archive = await makeArchive({ "доклад (замаскированный).docx": "docx-bytes" });
        await expect(unpackMaskArchive(archive)).rejects.toThrow(
            "Архив не содержит замаскированный файл и ключи демаскировки.",
        );
    });
});
