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

function manifest(operation: "mask_file" | "unmask_file"): string {
    return JSON.stringify({
        schema_version: 1,
        operation,
        status: "succeeded",
        complete: true,
        masking_ruleset_version: 1,
        exception_config_digest: "digest",
        input: { sha256: "abc", size_bytes: 10 },
        output: { document_name: "doc.docx", keys_name: "doc.yaml" },
        statistics: {
            total_occurrences: 1,
            occurrences_by_label: { person: 1 },
            unique_by_label: { person: 1 },
            unused_keys: ["ООО «Рога и копыта»"],
        },
        coverage: {
            processed_parts: ["main_document"],
            unprocessed_text_parts: [],
            rejected_parts: [],
            raster_image_ocr: "not_performed",
        },
        cache: { hit: false, namespace_version: 1 },
    });
}

describe("unpackMaskArchive", () => {
    it("returns the masked document, keys and validated manifest", async () => {
        const archive = await makeArchive({
            "доклад (замаскированный).docx": "docx-bytes",
            "доклад (ключи демаскировки).yaml": "Физлица:\n  - Иван Петров",
            "manifest.json": manifest("mask_file"),
        });

        const bundle = await unpackMaskArchive(archive);

        expect(bundle.resultName).toBe("доклад (замаскированный).docx");
        expect(bundle.keysName).toBe("доклад (ключи демаскировки).yaml");
        expect(await bundle.result.text()).toBe("docx-bytes");
        expect(await bundle.keys.text()).toContain("Иван Петров");
        expect(bundle.manifest.statistics.total_occurrences).toBe(1);
    });

    it("throws when a half or the manifest is missing", async () => {
        const noManifest = await makeArchive({
            "доклад (замаскированный).docx": "docx-bytes",
            "доклад (ключи демаскировки).yaml": "Физлица:\n  - Иван Петров",
        });
        await expect(unpackMaskArchive(noManifest)).rejects.toThrow("отчёт о результате");
        const noDocx = await makeArchive({
            "доклад (ключи демаскировки).yaml": "Физлица:\n  - Иван Петров",
            "manifest.json": manifest("mask_file"),
        });
        await expect(unpackMaskArchive(noDocx)).rejects.toThrow("отчёт о результате");
    });

    it("rejects a wrong manifest schema version", async () => {
        const archive = await makeArchive({
            "доклад (замаскированный).docx": "docx-bytes",
            "доклад (ключи демаскировки).yaml": "Физлица:\n  - Иван Петров",
            "manifest.json": manifest("mask_file").replace(
                '"schema_version":1',
                '"schema_version":99',
            ),
        });
        await expect(unpackMaskArchive(archive)).rejects.toThrow("Неподдерживаемая версия");
    });
});

describe("unpackRestoreArchive", () => {
    it("returns the restored document and the unused keys from the manifest", async () => {
        const archive = await makeArchive({
            "доклад (демаскированный).docx": "docx-bytes",
            "manifest.json": manifest("unmask_file"),
        });

        const bundle = await unpackRestoreArchive(archive);

        expect(bundle.docxName).toBe("доклад (демаскированный).docx");
        expect(bundle.manifestName).toBe("manifest.json");
        expect(await bundle.docx.text()).toBe("docx-bytes");
        expect(bundle.manifest.statistics.unused_keys).toEqual(["ООО «Рога и копыта»"]);
    });

    it("throws when the manifest is missing or broken", async () => {
        const missing = await makeArchive({ "доклад (демаскированный).docx": "docx-bytes" });
        await expect(unpackRestoreArchive(missing)).rejects.toThrow("отчёт о результате");
        const broken = await makeArchive({
            "доклад (демаскированный).docx": "docx-bytes",
            "manifest.json": "{ not json",
        });
        await expect(unpackRestoreArchive(broken)).rejects.toThrow("Отчёт о результате повреждён.");
    });
});
