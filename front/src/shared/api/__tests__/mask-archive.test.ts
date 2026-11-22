import { describe, expect, it } from "bun:test";

import JSZip from "jszip";

import { unpackMaskArchive, unpackRestoreArchive } from "@/shared/api/mask-archive";

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

describe("unpackRestoreArchive", () => {
    it("returns the restored document and the unused-keys manifest", async () => {
        const archive = await makeArchive({
            "доклад (демаскированный).docx": "docx-bytes",
            "доклад (отчёт о демаскировке).json": '{"unused_keys": ["ООО «Рога и копыта»"]}',
        });

        const bundle = await unpackRestoreArchive(archive);

        expect(bundle.docxName).toBe("доклад (демаскированный).docx");
        expect(bundle.manifestName).toBe("доклад (отчёт о демаскировке).json");
        expect(await bundle.docx.text()).toBe("docx-bytes");
        expect(bundle.manifest.unused_keys).toEqual(["ООО «Рога и копыта»"]);
    });

    it("throws when the manifest is missing or broken", async () => {
        const missing = await makeArchive({ "доклад (демаскированный).docx": "docx-bytes" });
        await expect(unpackRestoreArchive(missing)).rejects.toThrow(
            "Архив не содержит демаскированный файл и отчёт о демаскировке.",
        );
        const broken = await makeArchive({
            "доклад (демаскированный).docx": "docx-bytes",
            "доклад (отчёт о демаскировке).json": "{ not json",
        });
        await expect(unpackRestoreArchive(broken)).rejects.toThrow(
            "Отчёт о демаскировке повреждён.",
        );
    });
});
