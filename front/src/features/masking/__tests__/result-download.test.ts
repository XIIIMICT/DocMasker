import { afterEach, beforeEach, describe, expect, it, mock, spyOn } from "bun:test";

import {
    downloadKeys,
    downloadMaskedFile,
    downloadMaskedText,
    downloadRestored,
} from "../result-download";

let anchor: { href: string; download: string; click: () => void };
let createdUrl: Blob | null;
let revokedUrl: string | null;

beforeEach(() => {
    anchor = { href: "", download: "", click: mock(() => {}) };
    createdUrl = null;
    revokedUrl = null;
    spyOn(URL, "createObjectURL").mockImplementation((blob: Blob) => {
        createdUrl = blob;
        return "blob:test";
    });
    spyOn(URL, "revokeObjectURL").mockImplementation((url: string) => {
        revokedUrl = url;
    });
    spyOn(document, "createElement").mockImplementation(() => anchor as unknown as HTMLElement);
});

afterEach(() => {
    mock.restore();
});

const maskedFile = {
    result: new Blob(["docx"], { type: "application/octet-stream" }),
    resultName: "doc (замаскированный).docx",
    keys: new Blob(["keys"], { type: "application/yaml" }),
    keysName: "doc (ключи демаскировки).yaml",
};
const restoredFile = {
    blob: new Blob(["docx"], { type: "application/octet-stream" }),
    name: "doc (демаскированный).docx",
};

describe("download helpers", () => {
    it("names a downloaded masked text from the input base", async () => {
        downloadMaskedText("[PERSON_1]", "doc");
        expect(anchor.download).toBe("doc (замаскированный).txt");
        expect(anchor.href).toBe("blob:test");
        expect(createdUrl).toBeInstanceOf(Blob);
        expect(await (createdUrl as Blob).text()).toBe("[PERSON_1]");
        expect(revokedUrl).toBe("blob:test");
        expect(anchor.click).toHaveBeenCalledTimes(1);
    });

    it("uses archive names verbatim for file artifacts", () => {
        downloadMaskedFile(maskedFile);
        expect(anchor.download).toBe("doc (замаскированный).docx");
        expect(createdUrl).toBe(maskedFile.result);

        downloadKeys("keys", maskedFile, "doc");
        expect(anchor.download).toBe("doc (ключи демаскировки).yaml");
        expect(createdUrl).toBe(maskedFile.keys);

        downloadRestored("", restoredFile, "doc");
        expect(anchor.download).toBe("doc (демаскированный).docx");
        expect(createdUrl).toBe(restoredFile.blob);
    });

    it("derives text names when no archive artifact exists", async () => {
        downloadMaskedText("[PERSON_1]", null);
        expect(anchor.download).toBe("текст (замаскированный).txt");

        downloadKeys("keys", null, "doc");
        expect(anchor.download).toBe("doc (ключи демаскировки).yaml");
        expect((createdUrl as Blob).type).toContain("application/yaml");

        downloadRestored("VALUE-A", null, null);
        expect(anchor.download).toBe("текст (демаскированный).txt");
        expect(await (createdUrl as Blob).text()).toBe("VALUE-A");
    });

    it("does not download empty artifacts", () => {
        downloadMaskedText("", "doc");
        downloadKeys("", null, "doc");
        downloadRestored("", null, "doc");
        downloadMaskedFile(null);
        expect(anchor.click).not.toHaveBeenCalled();
        expect(createdUrl).toBeNull();
    });
});
