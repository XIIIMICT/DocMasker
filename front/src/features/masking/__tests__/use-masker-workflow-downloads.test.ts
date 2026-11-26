import { afterEach, beforeEach, describe, expect, it, jest, mock, spyOn } from "bun:test";
import type { Mock } from "bun:test";

import { act, renderHook } from "@testing-library/react";

import type { DocxResult, MaskedFileResult, ModeState } from "@/features/masking/types";
import type { MaskStats } from "@/shared/mask-stats";

import { useMaskerWorkflow } from "../use-masker-workflow";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ORIGINAL_FETCH = globalThis.fetch;

const STATS_ONE: MaskStats = {
    total: 1,
    byLabel: { person: 1 },
    uniqueByLabel: { person: 1 },
    unused: [],
};
const STATS_ZERO: MaskStats = {
    total: 0,
    byLabel: {},
    uniqueByLabel: {},
    unused: [],
};
const KEYS = "Физлица:\n  - VALUE-A";
const VALID_DEMASK_KEYS = KEYS;
const INVALID_DEMASK_KEYS = "Некорректный:\n  - VALUE-A";

const MASKED_FILE: MaskedFileResult = {
    result: new Blob(["masked-docx"], { type: "application/octet-stream" }),
    resultName: "doc (замаскированный).docx",
    keys: new Blob([KEYS], { type: "application/yaml" }),
    keysName: "doc (ключи демаскировки).yaml",
};
const RESTORED_FILE: DocxResult = {
    blob: new Blob(["restored-docx"], { type: "application/octet-stream" }),
    name: "doc (демаскированный).docx",
};

function fileArtifact(name = "doc.txt"): File {
    // A .txt keeps the DOCX placeholder-count effect out of this test while
    // still putting the workflow into file mode (`inFileMode = selectedFile !== null`).
    return new File(["data"], name, { type: "text/plain" });
}

let clicks: Array<{ download: string; href: string }>;
let createdBlob: Blob | null;
let clickSpy: Mock<(this: HTMLAnchorElement) => void>;

function setup(seed: Partial<ModeState> = {}, mode: "mask" | "unmask" = "mask") {
    const hook = renderHook(() => useMaskerWorkflow());
    if (mode === "unmask") act(() => hook.result.current.onModeChange("unmask"));
    act(() => hook.result.current.update(seed));
    return hook;
}

beforeEach(() => {
    jest.useFakeTimers();
    window.localStorage.clear();
    window.sessionStorage.clear();
    clicks = [];
    createdBlob = null;
    spyOn(URL, "createObjectURL").mockImplementation((blob: Blob | MediaSource) => {
        createdBlob = blob as Blob;
        return "blob:test";
    });
    spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    clickSpy = spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(function (
        this: HTMLAnchorElement,
    ) {
        clicks.push({ download: this.download, href: this.href });
    });
});

afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
    mock.restore();
    globalThis.fetch = ORIGINAL_FETCH;
    window.localStorage.clear();
    window.sessionStorage.clear();
});

function expectNoDownload() {
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(clickSpy).not.toHaveBeenCalled();
}

describe("downloadResult — authoritative operation boundary", () => {
    it("blocks when the run ended in an error", () => {
        const { result } = setup({
            input: "данные",
            result: "[PERSON_1]",
            stats: STATS_ONE,
            error: "Не удалось выполнить маскирование.",
        });
        expect(result.current.canDownloadResult).toBe(false);
        act(() => result.current.downloadResult());
        expectNoDownload();
    });

    it("blocks when masking produced nothing", () => {
        const { result } = setup({
            input: "данные",
            result: "[PERSON_1]",
            stats: STATS_ZERO,
        });
        expect(result.current.canDownloadResult).toBe(false);
        act(() => result.current.downloadResult());
        expectNoDownload();
    });

    it("blocks with no result at all", () => {
        const { result } = setup({ input: "данные" });
        expect(result.current.canDownloadResult).toBe(false);
        act(() => result.current.downloadResult());
        expectNoDownload();
    });

    it("blocks in file mode when the document artifact is missing", () => {
        const { result } = setup({
            selectedFile: fileArtifact(),
            result: "[PERSON_1]",
            stats: STATS_ONE,
        });
        expect(result.current.canDownloadResult).toBe(false);
        act(() => result.current.downloadResult());
        expectNoDownload();
    });

    it("does not gate a finished result on the busy flag", () => {
        const { result } = setup({
            input: "данные",
            result: "[PERSON_1]",
            stats: STATS_ONE,
            busy: true,
        });
        expect(result.current.canDownloadResult).toBe(true);
        act(() => result.current.downloadResult());
        expect(clickSpy).toHaveBeenCalledTimes(1);
    });

    it("downloads the masked text with the derived file name", async () => {
        const { result } = setup({
            input: "данные",
            textBaseName: "договор",
            result: "[PERSON_1]",
            stats: STATS_ONE,
        });
        act(() => result.current.downloadResult());

        expect(clickSpy).toHaveBeenCalledTimes(1);
        expect(clicks[0]).toEqual({ download: "договор (замаскированный).txt", href: "blob:test" });
        expect(createdBlob).toBeInstanceOf(Blob);
        expect(await (createdBlob as Blob).text()).toBe("[PERSON_1]");
    });

    it("downloads the masked document with the archive name", () => {
        const { result } = setup({ selectedFile: fileArtifact(), maskedFile: MASKED_FILE });
        act(() => result.current.downloadResult());

        expect(clicks[0]).toEqual({
            download: "doc (замаскированный).docx",
            href: "blob:test",
        });
        expect(createdBlob).toBe(MASKED_FILE.result);
    });
});

describe("downloadKeys — authoritative operation boundary", () => {
    it("blocks when the run ended in an error", () => {
        const { result } = setup({
            input: "данные",
            result: "[PERSON_1]",
            keys: KEYS,
            stats: STATS_ONE,
            error: "ошибка",
        });
        expect(result.current.canDownloadKeys).toBe(false);
        act(() => result.current.downloadKeys());
        expectNoDownload();
    });

    it("blocks with missing or empty keys", () => {
        const empty = setup({ input: "данные", keys: "" });
        expect(empty.result.current.canDownloadKeys).toBe(false);
        act(() => empty.result.current.downloadKeys());
        expectNoDownload();

        const missing = setup({ input: "данные" });
        expect(missing.result.current.canDownloadKeys).toBe(false);
        act(() => missing.result.current.downloadKeys());
        expectNoDownload();
    });

    it("downloads text keys as a YAML blob", async () => {
        const { result } = setup({ input: "данные", textBaseName: "договор", keys: KEYS });
        act(() => result.current.downloadKeys());

        expect(clicks[0]?.download).toBe("договор (ключи демаскировки).yaml");
        expect(createdBlob?.type).toContain("application/yaml");
        expect(await (createdBlob as Blob).text()).toBe(KEYS);
    });

    it("downloads the archive keys with the archive name", () => {
        const { result } = setup({
            selectedFile: fileArtifact(),
            maskedFile: MASKED_FILE,
            keys: KEYS,
        });
        act(() => result.current.downloadKeys());

        expect(clicks[0]?.download).toBe("doc (ключи демаскировки).yaml");
        expect(createdBlob).toBe(MASKED_FILE.keys);
    });
});

describe("downloadRestored — authoritative operation boundary", () => {
    it("blocks when the run ended in an error", () => {
        const { result } = setup(
            { input: "[PERSON_1]", restored: "VALUE-A", error: "ошибка" },
            "unmask",
        );
        expect(result.current.canDownloadRestored).toBe(false);
        act(() => result.current.downloadRestored());
        expectNoDownload();
    });

    it("blocks with no restored artifact", () => {
        const { result } = setup({ input: "[PERSON_1]" }, "unmask");
        expect(result.current.canDownloadRestored).toBe(false);
        act(() => result.current.downloadRestored());
        expectNoDownload();
    });

    it("blocks in file mode when the restored document is missing", () => {
        const { result } = setup({ selectedFile: fileArtifact(), restored: "VALUE-A" }, "unmask");
        expect(result.current.canDownloadRestored).toBe(false);
        act(() => result.current.downloadRestored());
        expectNoDownload();
    });

    it("downloads the restored text with the derived file name", async () => {
        const { result } = setup(
            { input: "[PERSON_1]", textBaseName: "договор", restored: "VALUE-A" },
            "unmask",
        );
        act(() => result.current.downloadRestored());

        expect(clicks[0]?.download).toBe("договор (демаскированный).txt");
        expect(await (createdBlob as Blob).text()).toBe("VALUE-A");
    });

    it("downloads the restored document with the archive name", () => {
        const { result } = setup(
            { selectedFile: fileArtifact(), restoredFile: RESTORED_FILE },
            "unmask",
        );
        act(() => result.current.downloadRestored());

        expect(clicks[0]?.download).toBe("doc (демаскированный).docx");
        expect(createdBlob).toBe(RESTORED_FILE.blob);
    });
});

describe("mask/unmask operation boundaries remain authoritative", () => {
    it("does not mask while busy or without input", async () => {
        const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((() =>
            Promise.resolve(new Response("{}", { status: 200 }))) as unknown as typeof fetch);
        const busy = setup({ input: "данные", busy: true });
        await act(async () => {
            await busy.result.current.handleMask();
        });
        const empty = setup({ input: "" });
        await act(async () => {
            await empty.result.current.handleMask();
        });
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("blocks demasking on busy, missing input, empty keys and invalid keys", async () => {
        const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((() =>
            Promise.resolve(new Response("{}", { status: 200 }))) as unknown as typeof fetch);
        const cases = [
            { input: "[PERSON_1]", demaskKeys: VALID_DEMASK_KEYS, busy: true },
            { input: "", demaskKeys: VALID_DEMASK_KEYS },
            { input: "[PERSON_1]", demaskKeys: "" },
            { input: "[PERSON_1]", demaskKeys: INVALID_DEMASK_KEYS },
        ];
        for (const seed of cases) {
            const { result } = setup(seed, "unmask");
            await act(async () => {
                await result.current.handleUnmask();
            });
        }
        expect(fetchSpy).not.toHaveBeenCalled();
    });

    it("still starts a ready demasking operation", async () => {
        const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((() =>
            Promise.resolve(
                new Response(JSON.stringify({ text: "VALUE-A" }), {
                    status: 200,
                    headers: { "Content-Type": "application/json" },
                }),
            )) as unknown as typeof fetch);
        const { result } = setup({ input: "[PERSON_1]", demaskKeys: VALID_DEMASK_KEYS }, "unmask");
        await act(async () => {
            await result.current.handleUnmask();
        });
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        expect(result.current.restored).toBe("VALUE-A");
    });
});
