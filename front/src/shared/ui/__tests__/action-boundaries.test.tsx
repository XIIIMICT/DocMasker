import { afterEach, beforeEach, describe, expect, it, jest, mock, spyOn } from "bun:test";
import type { Mock } from "bun:test";

import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";

import { useMaskerWorkflow } from "@/features/masking/use-masker-workflow";
import { DemaskInputPane, MaskInputPane } from "@/shared/ui/input-panes";
import type { DragHandlers } from "@/shared/ui/pane-types";
import { MaskResultPane, UnmaskResultPane } from "@/shared/ui/result-panes";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// react-aria's keyboard press path reads these constructors from the global
// scope; happy-dom provides them on `window` only.
globalThis.HTMLButtonElement = window.HTMLButtonElement;
globalThis.HTMLAnchorElement = window.HTMLAnchorElement;

const ORIGINAL_FETCH = globalThis.fetch;
const KEYS = "Физлица:\n  - VALUE-A";
const STATS = {
    total: 1,
    byLabel: { person: 1 },
    uniqueByLabel: { person: 1 },
    unused: [] as string[],
};

type Workflow = ReturnType<typeof useMaskerWorkflow>;
type PressSpies = {
    mask: Mock<() => void>;
    unmask: Mock<() => void>;
    result: Mock<() => void>;
    keys: Mock<() => void>;
    restored: Mock<() => void>;
};

let workflow!: Workflow;
let view: ReturnType<typeof render> | undefined;
let fetchSpy: Mock<typeof fetch>;
let clickSpy: Mock<(this: HTMLAnchorElement) => void>;
let clicks: Array<{ download: string; href: string }>;

function Harness({ press }: { press: PressSpies }) {
    workflow = useMaskerWorkflow();
    const inputTextRef = useRef<HTMLTextAreaElement>(null);
    const resultTextRef = useRef<HTMLTextAreaElement>(null);
    const sourceDocRef = useRef<HTMLDivElement>(null);
    const maskedDocRef = useRef<HTMLDivElement>(null);
    const restoredDocRef = useRef<HTMLDivElement>(null);
    const drag: DragHandlers = {
        dragging: false,
        onDragEnter: () => undefined,
        onDragOver: () => undefined,
        onDragLeave: () => undefined,
        onDrop: () => undefined,
    };

    return (
        <>
            <MaskInputPane
                input={workflow.input}
                inputTextRef={inputTextRef}
                sourceDocRef={sourceDocRef}
                selectedFile={workflow.selectedFile}
                inFileMode={workflow.inFileMode}
                busy={workflow.busy}
                maskEnabled={workflow.maskEnabled}
                activeJob={workflow.activeJob !== null}
                drag={drag}
                onTextChange={workflow.onTextChange}
                onFileActionPress={() => undefined}
                onMask={press.mask}
            />
            <DemaskInputPane
                input={workflow.input}
                inputTextRef={inputTextRef}
                sourceDocRef={sourceDocRef}
                selectedFile={workflow.selectedFile}
                inFileMode={workflow.inFileMode}
                busy={workflow.busy}
                unmaskEnabled={workflow.unmaskEnabled}
                activeJob={workflow.activeJob !== null}
                keysInvalid={workflow.keysInvalid}
                demaskKeys={workflow.demaskKeys}
                inputTab={workflow.inputTab}
                demaskStats={workflow.demaskStats}
                drag={drag}
                onTextChange={workflow.onTextChange}
                onDemaskKeysChange={(value) => workflow.update({ demaskKeys: value })}
                onKeysDrop={() => undefined}
                onInputTabChange={(key) => workflow.update({ inputTab: key })}
                onDataFilePress={() => undefined}
                onKeysFilePress={() => undefined}
                onUnmask={press.unmask}
            />
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
                onDownloadResult={press.result}
                onDownloadKeys={press.keys}
            />
            <UnmaskResultPane
                error={workflow.error}
                restored={workflow.restored}
                restoredFile={workflow.restoredFile}
                restoredDocRef={restoredDocRef}
                parsedDemaskKeys={workflow.parsedDemaskKeys}
                inFileMode={workflow.inFileMode}
                canDownload={workflow.canDownloadRestored}
                onDownload={press.restored}
            />
        </>
    );
}

function renderPanes(): PressSpies {
    // Each spy stands in for the handler wired by the page: it records that a
    // key press reached the action, then calls the real guarded workflow
    // handler. A visually disabled button can still fire `onPress` in HeroUI
    // 3.2.8, so the operation boundary — not `aria-disabled` — must block it.
    const press: PressSpies = {
        mask: mock(() => {
            void workflow.handleMask();
        }),
        unmask: mock(() => {
            void workflow.handleUnmask();
        }),
        result: mock(() => workflow.downloadResult()),
        keys: mock(() => workflow.downloadKeys()),
        restored: mock(() => workflow.downloadRestored()),
    };
    view = render(<Harness press={press} />);
    return press;
}

function action(name: string): HTMLElement {
    return screen.getByRole("button", { name });
}

function pressKey(element: HTMLElement, key: "Enter" | " ") {
    fireEvent.keyDown(element, { key });
    fireEvent.keyUp(element, { key });
}

/** Flush the microtasks of a press-triggered async operation inside `act`. */
async function flush() {
    await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
    });
}

beforeEach(() => {
    jest.useFakeTimers();
    window.localStorage.clear();
    window.sessionStorage.clear();
    clicks = [];
    fetchSpy = spyOn(globalThis, "fetch").mockImplementation((() =>
        Promise.resolve(
            new Response(
                JSON.stringify({
                    text: "[PERSON_1]",
                    keys: KEYS,
                    stats: {
                        total: 1,
                        occurrences_by_label: { person: 1 },
                        unique_by_label: { person: 1 },
                    },
                }),
                { status: 200, headers: { "Content-Type": "application/json" } },
            ),
        )) as unknown as typeof fetch);
    spyOn(URL, "createObjectURL").mockImplementation(() => "blob:test");
    spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    clickSpy = spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(function (
        this: HTMLAnchorElement,
    ) {
        clicks.push({ download: this.download, href: this.href });
    });
});

afterEach(() => {
    view?.unmount();
    view = undefined;
    jest.clearAllTimers();
    jest.useRealTimers();
    mock.restore();
    globalThis.fetch = ORIGINAL_FETCH;
    window.localStorage.clear();
    window.sessionStorage.clear();
});

describe("visually disabled actions — keyboard boundary", () => {
    it("exposes aria-disabled on Mask and download buttons and blocks Enter/Space operations", async () => {
        const press = renderPanes();
        const buttons = [
            action("Замаскировать"),
            action("Скачать результат маскировки"),
            action("Скачать ключи демаскировки"),
        ];
        for (const button of buttons) {
            expect(button.getAttribute("aria-disabled")).toBe("true");
            expect(button.hasAttribute("disabled")).toBe(false);
            // The workaround keeps the control focusable: assistive tech must
            // hear `aria-disabled`, and the authoritative guard must hold.
            expect(button.tabIndex).toBe(0);
        }

        for (const button of buttons) {
            pressKey(button, "Enter");
            pressKey(button, " ");
        }
        expect(press.mask).toHaveBeenCalledTimes(2);
        expect(press.result).toHaveBeenCalledTimes(2);
        expect(press.keys).toHaveBeenCalledTimes(2);
        expect(fetchSpy).not.toHaveBeenCalled();
        expect(URL.createObjectURL).not.toHaveBeenCalled();
        expect(clickSpy).not.toHaveBeenCalled();
        await flush();
    });

    it("blocks Enter/Space on a visually disabled Unmask button", async () => {
        const press = renderPanes();
        act(() =>
            workflow.update({
                input: "[PERSON_1]",
                demaskKeys: "Некорректный:\n  - VALUE-A",
            }),
        );
        const unmask = action("Демаскировать");
        expect(unmask.getAttribute("aria-disabled")).toBe("true");
        expect(unmask.tabIndex).toBe(0);

        pressKey(unmask, "Enter");
        pressKey(unmask, " ");
        expect(press.unmask).toHaveBeenCalledTimes(2);
        expect(fetchSpy).not.toHaveBeenCalled();
        await flush();
    });

    it("does not treat the CSS pointer-events workaround as a guard", async () => {
        // jsdom/happy-dom does not load globals.css, so a synthetic click reaches
        // `onPress` exactly as a forced DOM event would in a real browser. The
        // workflow boundary — not `pointer-events: none` — must stop the work.
        const press = renderPanes();
        // Keep both inputs empty: mask, demask and all downloads stay visually
        // disabled, so every side effect can only come from the boundary check.
        const buttons = [
            action("Замаскировать"),
            action("Скачать результат маскировки"),
            action("Скачать ключи демаскировки"),
            action("Демаскировать"),
        ];
        for (const button of buttons) fireEvent.click(button);
        for (const spy of [press.mask, press.result, press.keys, press.unmask]) {
            expect(spy).toHaveBeenCalledTimes(1);
        }
        expect(fetchSpy).not.toHaveBeenCalled();
        expect(URL.createObjectURL).not.toHaveBeenCalled();
        expect(clickSpy).not.toHaveBeenCalled();
        await flush();
    });
});

describe("enabled actions — keyboard usable", () => {
    function seedReadyState() {
        act(() =>
            workflow.update({
                input: "данные",
                textBaseName: "договор",
                result: "[PERSON_1]",
                keys: KEYS,
                stats: STATS,
                restored: "VALUE-A",
                demaskKeys: KEYS,
            }),
        );
    }

    it("keeps enabled Mask and download controls focusable and keyboard-operable", async () => {
        const press = renderPanes();
        seedReadyState();
        const buttons = {
            mask: action("Замаскировать"),
            result: action("Скачать результат маскировки"),
            keys: action("Скачать ключи демаскировки"),
            restored: action("Скачать файл"),
        };
        for (const button of Object.values(buttons)) {
            expect(button.getAttribute("aria-disabled")).toBe("false");
            expect(button.hasAttribute("disabled")).toBe(false);
            expect(button.tabIndex).toBe(0);
        }

        pressKey(buttons.result, " ");
        pressKey(buttons.keys, "Enter");
        pressKey(buttons.restored, " ");
        expect(clicks.map((entry) => entry.download)).toEqual([
            "договор (замаскированный).txt",
            "договор (ключи демаскировки).yaml",
            "договор (демаскированный).txt",
        ]);

        pressKey(buttons.mask, "Enter");
        expect(press.mask).toHaveBeenCalledTimes(1);
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        await flush();
    });

    it("keeps the enabled Unmask control focusable and keyboard-operable", async () => {
        const press = renderPanes();
        seedReadyState();
        const unmask = action("Демаскировать");
        expect(unmask.getAttribute("aria-disabled")).toBe("false");
        expect(unmask.hasAttribute("disabled")).toBe(false);
        expect(unmask.tabIndex).toBe(0);

        pressKey(unmask, " ");
        expect(press.unmask).toHaveBeenCalledTimes(1);
        expect(fetchSpy).toHaveBeenCalledTimes(1);
        await flush();
    });
});
