import { afterEach, beforeEach, describe, expect, it, jest, mock, spyOn } from "bun:test";
import type { Mock } from "bun:test";

import {
    downloadKeys,
    downloadMaskedFile,
    downloadMaskedText,
    downloadRestored,
} from "../result-download";

let anchor: HTMLAnchorElement;
let clickSpy: Mock<() => void>;
let createdBlob: Blob | null;
let revoked: string[];
let revokedDuringClick: string[];
let connectedDuringClick: boolean;

beforeEach(() => {
    jest.useFakeTimers();
    createdBlob = null;
    revoked = [];
    revokedDuringClick = [];
    connectedDuringClick = false;
    spyOn(URL, "createObjectURL").mockImplementation((blob: Blob) => {
        createdBlob = blob;
        return "blob:test";
    });
    spyOn(URL, "revokeObjectURL").mockImplementation((url: string) => {
        revoked.push(url);
    });
    anchor = document.createElement("a");
    clickSpy = spyOn(anchor, "click");
    clickSpy.mockImplementation(() => {
        revokedDuringClick = [...revoked];
        connectedDuringClick = document.body.contains(anchor);
    });
    spyOn(document, "createElement").mockImplementation(() => anchor);
});

afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    mock.restore();
    anchor.remove();
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
    it("names a downloaded masked text from the input base and defers revocation", async () => {
        downloadMaskedText("[PERSON_1]", "doc");
        expect(anchor.download).toBe("doc (замаскированный).txt");
        expect(anchor.href).toBe("blob:test");
        expect(createdBlob).toBeInstanceOf(Blob);
        expect(await (createdBlob as Blob).text()).toBe("[PERSON_1]");
        expect(clickSpy).toHaveBeenCalledTimes(1);
        // The URL must survive the click: the browser starts reading the Blob
        // in a later task, so revoking before that can cancel the download.
        expect(revokedDuringClick).toEqual([]);
        expect(revoked).toEqual([]);
        expect(connectedDuringClick).toBe(true);
        expect(anchor.isConnected).toBe(false);
        jest.advanceTimersByTime(0);
        expect(revoked).toEqual(["blob:test"]);
    });

    it("uses archive names verbatim for file artifacts", () => {
        downloadMaskedFile(maskedFile);
        expect(anchor.download).toBe("doc (замаскированный).docx");
        expect(createdBlob).toBe(maskedFile.result);

        downloadKeys("keys", maskedFile, "doc");
        expect(anchor.download).toBe("doc (ключи демаскировки).yaml");
        expect(createdBlob).toBe(maskedFile.keys);

        downloadRestored("", restoredFile, "doc");
        expect(anchor.download).toBe("doc (демаскированный).docx");
        expect(createdBlob).toBe(restoredFile.blob);
        jest.runAllTimers();
        expect(revoked).toEqual(["blob:test", "blob:test", "blob:test"]);
    });

    it("derives text names when no archive artifact exists", async () => {
        downloadMaskedText("[PERSON_1]", null);
        expect(anchor.download).toBe("текст (замаскированный).txt");

        downloadKeys("keys", null, "doc");
        expect(anchor.download).toBe("doc (ключи демаскировки).yaml");
        expect((createdBlob as Blob).type).toContain("application/yaml");

        downloadRestored("VALUE-A", null, null);
        expect(anchor.download).toBe("текст (демаскированный).txt");
        expect(await (createdBlob as Blob).text()).toBe("VALUE-A");
        jest.runAllTimers();
        expect(revoked).toEqual(["blob:test", "blob:test", "blob:test"]);
    });

    it("does not download empty artifacts", () => {
        downloadMaskedText("", "doc");
        downloadKeys("", null, "doc");
        downloadRestored("", null, "doc");
        downloadMaskedFile(null);
        expect(clickSpy).not.toHaveBeenCalled();
        expect(createdBlob).toBeNull();
        expect(revoked).toEqual([]);
    });

    it("revokes and removes the anchor even when click throws", () => {
        const failure = new Error("click failed");
        clickSpy.mockImplementation(() => {
            throw failure;
        });
        expect(() => downloadMaskedText("[PERSON_1]", "doc")).toThrow(failure);
        // Cleanup is in `finally`, but the original error still propagates.
        expect(anchor.isConnected).toBe(false);
        expect(revoked).toEqual([]);
        jest.advanceTimersByTime(0);
        expect(revoked).toEqual(["blob:test"]);
    });
});
