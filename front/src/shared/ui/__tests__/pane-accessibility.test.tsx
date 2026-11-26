import { afterEach, describe, expect, it } from "bun:test";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createRef, type ComponentProps } from "react";

import { parseKeys } from "@/shared/masking/keys-parse";
import { DemaskInputPane, MaskInputPane } from "@/shared/ui/input-panes";
import type { DragHandlers } from "@/shared/ui/pane-types";
import { MaskResultPane, UnmaskResultPane } from "@/shared/ui/result-panes";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// react-aria's press path reads these constructors from the global scope;
// happy-dom installs them on `window` only.
globalThis.HTMLButtonElement = window.HTMLButtonElement;
globalThis.HTMLAnchorElement = window.HTMLAnchorElement;
globalThis.HTMLInputElement = window.HTMLInputElement;
globalThis.HTMLTextAreaElement = window.HTMLTextAreaElement;
globalThis.HTMLSelectElement = window.HTMLSelectElement;
globalThis.CSS = window.CSS;

const noop = () => undefined;
const drag: DragHandlers = {
    dragging: false,
    onDragEnter: noop,
    onDragOver: noop,
    onDragLeave: noop,
    onDrop: noop,
};

function renderMaskInput(patch: Partial<ComponentProps<typeof MaskInputPane>> = {}) {
    return render(
        <MaskInputPane
            input="Иван Петров"
            inputTextRef={createRef<HTMLTextAreaElement>()}
            sourceDocRef={createRef<HTMLDivElement>()}
            selectedFile={null}
            inFileMode={false}
            busy={false}
            maskEnabled={false}
            activeJob={false}
            drag={drag}
            onTextChange={noop}
            onFileActionPress={noop}
            onMask={noop}
            {...patch}
        />,
    );
}

function renderDemaskInput(patch: Partial<ComponentProps<typeof DemaskInputPane>> = {}) {
    return render(
        <DemaskInputPane
            input="[PERSON_1]"
            inputTextRef={createRef<HTMLTextAreaElement>()}
            sourceDocRef={createRef<HTMLDivElement>()}
            selectedFile={null}
            inFileMode={false}
            busy={false}
            unmaskEnabled={false}
            activeJob={false}
            keysInvalid={false}
            demaskKeys=""
            inputTab="data"
            demaskStats={null}
            drag={drag}
            onTextChange={noop}
            onDemaskKeysChange={noop}
            onKeysDrop={noop}
            onInputTabChange={noop}
            onDataFilePress={noop}
            onKeysFilePress={noop}
            onUnmask={noop}
            {...patch}
        />,
    );
}

function renderMaskResult(patch: Partial<ComponentProps<typeof MaskResultPane>> = {}) {
    return render(
        <MaskResultPane
            error={null}
            resultTab="result"
            onResultTabChange={noop}
            stats={null}
            keys=""
            maskedNothing={false}
            showResultPreview={false}
            maskedFile={null}
            inFileMode={false}
            result="[PERSON_1]"
            resultTextRef={createRef<HTMLTextAreaElement>()}
            maskedDocRef={createRef<HTMLDivElement>()}
            canDownloadResult={false}
            canDownloadKeys={false}
            onDownloadResult={noop}
            onDownloadKeys={noop}
            {...patch}
        />,
    );
}

function renderUnmaskResult(patch: Partial<ComponentProps<typeof UnmaskResultPane>> = {}) {
    return render(
        <UnmaskResultPane
            error={null}
            restored="Иван Петров"
            restoredFile={null}
            restoredDocRef={createRef<HTMLDivElement>()}
            parsedDemaskKeys={parseKeys("")}
            inFileMode={false}
            canDownload={false}
            onDownload={noop}
            {...patch}
        />,
    );
}

afterEach(() => cleanup());

describe("input pane accessible names", () => {
    it("names the mask text area and file action without a visible label", () => {
        renderMaskInput();
        expect(screen.getByRole("textbox", { name: "Данные для маскировки" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Выбрать файл" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Замаскировать" })).toBeTruthy();
    });

    it("names the demask data area, both file selectors and the action", () => {
        renderDemaskInput();
        expect(screen.getByRole("textbox", { name: "Данные для демаскировки" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Выбрать файл данных" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Выбрать файл ключей" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Демаскировать" })).toBeTruthy();
    });

    it("names both demask tabs and the keys editor behind the keys tab", () => {
        renderDemaskInput({ inputTab: "keys", demaskKeys: "Физлица:\n  - Иван" });
        expect(screen.getByRole("tab", { name: "Данные для демаскировки" })).toBeTruthy();
        expect(screen.getByRole("tab", { name: "Ключи демаскировки" })).toBeTruthy();
        expect(screen.getByRole("textbox", { name: "Ключи демаскировки" })).toBeTruthy();
    });

    it("marks invalid keys and ties the visible reason to the editor", () => {
        renderDemaskInput({ inputTab: "keys", keysInvalid: true, demaskKeys: "Ошибка:\n  - x" });
        const keys = screen.getByRole("textbox", { name: "Ключи демаскировки" });
        expect(keys.getAttribute("aria-invalid")).toBe("true");
        const error = screen.getByText("Некорректный формат.");
        expect(error.hidden).toBe(false);
        expect(keys.getAttribute("aria-describedby")).toBe(error.id);
    });
});

describe("result pane accessible names and error association", () => {
    it("names the tabs, result area and download actions", () => {
        renderMaskResult({ canDownloadResult: true, canDownloadKeys: true });
        expect(screen.getByRole("tab", { name: "Результат маскировки" })).toBeTruthy();
        expect(screen.getByRole("tab", { name: "Ключи демаскировки" })).toBeTruthy();
        expect(screen.getByRole("textbox", { name: "Результат" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Скачать результат маскировки" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Скачать ключи демаскировки" })).toBeTruthy();
    });

    it("names the read-only keys area when the keys tab is selected", () => {
        renderMaskResult({ resultTab: "keys", keys: "Физлица:\n  - Иван", canDownloadKeys: true });
        expect(screen.getByRole("textbox", { name: "Ключи демаскировки" })).toHaveProperty(
            "readOnly",
            true,
        );
    });

    it("explains disabled masking downloads through the error message", () => {
        renderMaskResult({ error: "Нет отчёта о результате" });
        const error = screen.getByRole("textbox", { name: "Ошибка" });
        expect(error.getAttribute("aria-invalid")).toBe("true");
        for (const name of ["Скачать результат маскировки", "Скачать ключи демаскировки"]) {
            expect(screen.getByRole("button", { name }).getAttribute("aria-describedby")).toBe(
                error.id,
            );
        }
    });

    it("explains the disabled demask download through the error message", () => {
        renderUnmaskResult({ error: "Нет отчёта о результате" });
        const error = screen.getByRole("textbox", { name: "Ошибка" });
        expect(
            screen.getByRole("button", { name: "Скачать файл" }).getAttribute("aria-describedby"),
        ).toBe(error.id);
    });
});

describe("tab keyboard operation", () => {
    it("changes the demask input tab with the arrow keys", () => {
        let selected = "data";
        renderDemaskInput({ onInputTabChange: (key) => (selected = String(key)) });
        const tab = screen.getByRole("tab", { name: "Данные для демаскировки" });
        fireEvent.keyDown(tab, { key: "ArrowRight" });
        expect(selected).toBe("keys");
    });

    it("changes the mask result tab with the arrow keys", () => {
        let selected = "result";
        renderMaskResult({
            canDownloadKeys: true,
            onResultTabChange: (key) => (selected = String(key)),
        });
        const tab = screen.getByRole("tab", { name: "Результат маскировки" });
        fireEvent.keyDown(tab, { key: "ArrowRight" });
        expect(selected).toBe("keys");
    });
});
