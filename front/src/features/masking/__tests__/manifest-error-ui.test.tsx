import { afterEach, beforeEach, describe, expect, it } from "bun:test";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import JSZip from "jszip";
import { useRef } from "react";

import { useMaskerWorkflow } from "@/features/masking/use-masker-workflow";
import { MaskResultPane } from "@/shared/ui/result-panes";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ORIGINAL_FETCH = globalThis.fetch;

type Workflow = ReturnType<typeof useMaskerWorkflow>;

let workflow!: Workflow;

function Harness() {
    workflow = useMaskerWorkflow();
    const resultTextRef = useRef<HTMLTextAreaElement>(null);
    const maskedDocRef = useRef<HTMLDivElement>(null);
    return (
        <>
            <button type="button" onClick={() => void workflow.handleMask()}>
                Замаскировать
            </button>
            <MaskResultPane
                error={workflow.error}
                resultTab={workflow.resultTab}
                onResultTabChange={(key) => workflow.update({ resultTab: key })}
                stats={workflow.stats}
                keys={workflow.keys}
                maskedNothing={workflow.maskedNothing}
                showResultPreview={workflow.showResultPreview}
                maskedFile={workflow.maskedFile}
                inFileMode={workflow.inFileMode}
                result={workflow.result}
                resultTextRef={resultTextRef}
                maskedDocRef={maskedDocRef}
                canDownloadResult={workflow.canDownloadResult}
                canDownloadKeys={workflow.canDownloadKeys}
                onDownloadResult={() => undefined}
                onDownloadKeys={() => undefined}
            />
        </>
    );
}

function manifest(overrides: Record<string, unknown>): string {
    return JSON.stringify({
        schema_version: 1,
        operation: "mask_file",
        status: "succeeded",
        complete: true,
        masking_ruleset_version: 1,
        exception_config_digest: "digest",
        input: { sha256: "abc", size_bytes: 10 },
        output: { document_name: "doc.docx", keys_name: "doc.yaml" },
        statistics: {
            total_occurrences: -3,
            occurrences_by_label: { person: 1 },
            unique_by_label: { person: 1 },
            unused_keys: ["Иван Петров"],
        },
        coverage: {
            processed_parts: ["main_document"],
            unprocessed_text_parts: [],
            rejected_parts: [],
            raster_image_ocr: "not_performed",
        },
        cache: { hit: false, namespace_version: 1 },
        ...overrides,
    });
}

async function archiveWith(manifestText: string): Promise<Blob> {
    const zip = new JSZip();
    zip.file("doc (замаскированный).docx", "docx-bytes");
    zip.file("doc (ключи демаскировки).yaml", "Физлица:\n  - VALUE-A");
    zip.file("manifest.json", manifestText);
    return zip.generateAsync({ type: "blob" });
}

async function runWith(manifestText: string): Promise<void> {
    const blob = await archiveWith(manifestText);
    globalThis.fetch = (async () =>
        new Response(blob, {
            status: 200,
            headers: { "Content-Type": "application/zip" },
        })) as unknown as typeof fetch;
    render(<Harness />);
    act(() => {
        workflow.update({ selectedFile: new File(["docx"], "doc.docx") });
    });
    fireEvent.click(screen.getByRole("button", { name: "Замаскировать" }));
    await waitFor(() => {
        expect(screen.queryByRole("textbox", { name: "Ошибка" })).not.toBeNull();
    });
}

beforeEach(() => {
    cleanup();
});

afterEach(() => {
    cleanup();
    globalThis.fetch = ORIGINAL_FETCH;
});

describe("manifest validation in the result UI", () => {
    it("shows the fixed safe error for an invalid count, never fabricated stats", async () => {
        await runWith(manifest({}));

        const error = screen.getByRole("textbox", { name: "Ошибка" }) as HTMLTextAreaElement;
        expect(error.value).toContain("Некорректный отчёт о результате.");
        expect(error.value).not.toContain("Иван Петров");
        expect(document.querySelector(".editor-pane__stats")).toBeNull();
        expect(workflow.stats).toBeNull();
    });

    it("shows the fixed safe error when a nested coverage array is missing", async () => {
        await runWith(
            manifest({
                coverage: {
                    processed_parts: ["main_document"],
                    rejected_parts: [],
                    raster_image_ocr: "not_performed",
                },
            }),
        );

        const error = screen.getByRole("textbox", { name: "Ошибка" }) as HTMLTextAreaElement;
        expect(error.value).toContain("Некорректный отчёт о результате.");
        expect(document.querySelector(".editor-pane__stats")).toBeNull();
    });
});
