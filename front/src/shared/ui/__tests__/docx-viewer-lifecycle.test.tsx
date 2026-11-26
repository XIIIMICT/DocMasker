import { afterEach, describe, expect, it, jest, spyOn } from "bun:test";

import { act, render, waitFor } from "@testing-library/react";

import { type DocxRender, DocxViewer, HIGHLIGHT_DEBOUNCE_MS } from "@/shared/ui/docx-viewer";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ERROR_TEXT = "Не удалось отобразить предпросмотр документа.";

const BLOB_A = new Blob(["A"]);
const BLOB_B = new Blob(["B"]);

type Deferred<T> = {
    promise: Promise<T>;
    resolve: (value: T | PromiseLike<T>) => void;
    reject: (reason?: unknown) => void;
};

/** Explicit promise control: the tests decide exactly when a render completes. */
function deferred<T = void>(): Deferred<T> {
    let resolve!: (value: T | PromiseLike<T>) => void;
    let reject!: (reason?: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

function surfaceOf(container: HTMLElement): HTMLElement {
    const surface = container.querySelector<HTMLElement>(".docx-viewer__surface");
    if (!surface) throw new Error("docx-viewer surface is missing");
    return surface;
}

function marks(surface: HTMLElement): string[] {
    return [...surface.querySelectorAll("mark.docx-marker")].map((mark) => mark.textContent ?? "");
}

const flush = () =>
    act(async () => {
        await Promise.resolve();
    });

afterEach(() => {
    jest.useRealTimers();
});

describe("DocxViewer async lifecycle", () => {
    it("renders each blob exactly once through the injected seam", async () => {
        const seen: Blob[] = [];
        const renderDocument: DocxRender = async (blob, container) => {
            seen.push(blob);
            container.innerHTML = "<p>document</p>";
        };

        const view = render(<DocxViewer blob={BLOB_A} renderDocument={renderDocument} />);
        await flush();
        expect(seen).toEqual([BLOB_A]);
        expect(surfaceOf(view.container).innerHTML).toBe("<p>document</p>");

        await act(async () => {
            view.rerender(<DocxViewer blob={BLOB_B} renderDocument={renderDocument} />);
        });
        expect(seen).toEqual([BLOB_A, BLOB_B]);
    });

    it("a stale render that completes after a blob switch cannot overwrite the newer DOM", async () => {
        const gate = deferred<void>();
        const renderDocument: DocxRender = async (blob, container, isCurrent) => {
            if (blob === BLOB_A) {
                // Stands in for the dynamic import: the pass is suspended
                // before it touches the DOM, exactly where production checks
                // the generation after `import("docx-preview")`.
                await gate.promise;
                if (!isCurrent()) return;
                container.innerHTML = "<p>[PERSON_1]</p>";
                return;
            }
            container.innerHTML = "<p>новый документ</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} highlight={["[PERSON_1]"]} renderDocument={renderDocument} />,
        );
        const surface = surfaceOf(view.container);
        expect(surface.innerHTML).toBe("");

        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_B}
                    highlight={["[PERSON_1]"]}
                    renderDocument={renderDocument}
                />,
            );
        });
        expect(surface.textContent).toBe("новый документ");

        await act(async () => {
            gate.resolve();
            await gate.promise;
        });

        expect(surface.textContent).toBe("новый документ");
        expect(marks(surface)).toEqual([]);
        expect(view.container.querySelector(".docx-viewer__error")).toBeNull();
    });

    it("a stale completion that ignores the guard never applies highlights or error state", async () => {
        const gateA = deferred<void>();
        const gateB = deferred<void>();
        const renderDocument: DocxRender = async (blob, container) => {
            if (blob === BLOB_A) {
                await gateA.promise;
                container.innerHTML = "<p>[PERSON_1]</p>";
                return;
            }
            await gateB.promise;
            container.innerHTML = "<p>новый документ</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} highlight={["[PERSON_1]"]} renderDocument={renderDocument} />,
        );
        const surface = surfaceOf(view.container);
        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_B}
                    highlight={["[PERSON_1]"]}
                    renderDocument={renderDocument}
                />,
            );
        });

        await act(async () => {
            gateB.resolve();
            await gateB.promise;
        });
        expect(surface.textContent).toBe("новый документ");
        expect(marks(surface)).toEqual([]); // no marker in B's document

        // A ignores the generation and paints into the live container. The
        // component's own post-render guard must refuse to highlight stale DOM.
        await act(async () => {
            gateA.resolve();
            await gateA.promise;
            await Promise.resolve();
        });
        expect(surface.textContent).toBe("[PERSON_1]");
        expect(marks(surface)).toEqual([]);
        expect(view.container.querySelector(".docx-viewer__error")).toBeNull();
    });

    it("a late completion after unmount touches neither DOM nor React state", async () => {
        const gate = deferred<void>();
        const errorSpy = spyOn(console, "error").mockImplementation(() => undefined);
        try {
            const renderDocument: DocxRender = async (_blob, container, isCurrent) => {
                await gate.promise;
                if (!isCurrent()) return; // production's post-import guard
                container.innerHTML = "<p>[PERSON_1]</p>";
            };

            const view = render(
                <DocxViewer
                    blob={BLOB_A}
                    highlight={["[PERSON_1]"]}
                    renderDocument={renderDocument}
                />,
            );
            const surface = surfaceOf(view.container);
            view.unmount();
            expect(surface.innerHTML).toBe("");

            await act(async () => {
                gate.resolve();
                await gate.promise;
            });

            expect(surface.innerHTML).toBe("");
            expect(marks(surface)).toEqual([]);
            expect(errorSpy).not.toHaveBeenCalled();
        } finally {
            errorSpy.mockRestore();
        }
    });

    it("a rejection that arrives after unmount is swallowed without a state warning", async () => {
        const gate = deferred<void>();
        const errorSpy = spyOn(console, "error").mockImplementation(() => undefined);
        try {
            const renderDocument: DocxRender = async (_blob, _container, isCurrent) => {
                await gate.promise;
                if (!isCurrent()) return;
                throw new Error("late failure");
            };

            const view = render(<DocxViewer blob={BLOB_A} renderDocument={renderDocument} />);
            view.unmount();

            await act(async () => {
                gate.reject(new Error("chunk failed after unmount"));
                await gate.promise.catch(() => undefined);
            });

            expect(errorSpy).not.toHaveBeenCalled();
        } finally {
            errorSpy.mockRestore();
        }
    });

    it("a rejected dynamic import surfaces the preview error", async () => {
        const renderDocument: DocxRender = () => Promise.reject(new Error("chunk unavailable"));
        const view = render(<DocxViewer blob={BLOB_A} renderDocument={renderDocument} />);

        await waitFor(() => {
            expect(view.container.querySelector(".docx-viewer__error")?.textContent).toBe(
                ERROR_TEXT,
            );
        });
        expect(surfaceOf(view.container).innerHTML).toBe("");
    });

    it("a failed renderAsync surfaces the preview error", async () => {
        let painted = false;
        const renderDocument: DocxRender = async (_blob, container) => {
            container.innerHTML = "<p>partial</p>";
            painted = true;
            throw new Error("render failed");
        };
        const view = render(<DocxViewer blob={BLOB_A} renderDocument={renderDocument} />);

        await waitFor(() => {
            expect(view.container.querySelector(".docx-viewer__error")?.textContent).toBe(
                ERROR_TEXT,
            );
        });
        expect(painted).toBe(true);
    });

    it("highlight-only changes reuse the render and never nest marks", async () => {
        jest.useFakeTimers();
        let renders = 0;
        const renderDocument: DocxRender = async (_blob, container) => {
            renders += 1;
            container.innerHTML = "<p>[PERSON_1] и [PERSON_2]</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} highlight={["[PERSON_1]"]} renderDocument={renderDocument} />,
        );
        await flush();
        const surface = surfaceOf(view.container);
        expect(renders).toBe(1);
        expect(marks(surface)).toEqual(["[PERSON_1]"]);

        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_A}
                    highlight={["[PERSON_1]", "[PERSON_2]"]}
                    renderDocument={renderDocument}
                />,
            );
        });
        expect(renders).toBe(1);
        expect(jest.getTimerCount()).toBe(1);
        await act(async () => {
            jest.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS);
        });

        expect(renders).toBe(1);
        expect(marks(surface)).toEqual(["[PERSON_1]", "[PERSON_2]"]);
        expect(surface.querySelectorAll("mark mark")).toHaveLength(0);

        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_A}
                    highlight={["[PERSON_2]"]}
                    renderDocument={renderDocument}
                />,
            );
        });
        await act(async () => {
            jest.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS);
        });

        expect(renders).toBe(1);
        expect(marks(surface)).toEqual(["[PERSON_2]"]);
        expect(surface.querySelectorAll("mark mark")).toHaveLength(0);
        expect(surface.textContent).toBe("[PERSON_1] и [PERSON_2]");
    });

    it("a highlight scheduled for an older generation is dropped after a blob switch", async () => {
        jest.useFakeTimers();
        const renderDocument: DocxRender = async (blob, container) => {
            container.innerHTML = blob === BLOB_A ? "<p>[PERSON_1]</p>" : "<p>другой</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} renderDocument={renderDocument} highlight={[]} />,
        );
        await flush();
        const surface = surfaceOf(view.container);

        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_A}
                    renderDocument={renderDocument}
                    highlight={["[PERSON_1]"]}
                />,
            );
        });
        expect(marks(surface)).toEqual([]); // debounce has not fired yet

        await act(async () => {
            view.rerender(
                <DocxViewer blob={BLOB_B} renderDocument={renderDocument} highlight={[]} />,
            );
        });
        await act(async () => {
            jest.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS + 10);
        });

        expect(marks(surface)).toEqual([]);
        expect(surface.textContent).toBe("другой");
    });

    it("unmount cancels the pending highlight timer and leaves no callbacks behind", async () => {
        jest.useFakeTimers();
        const renderDocument: DocxRender = async (_blob, container) => {
            container.innerHTML = "<p>[PERSON_1]</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} renderDocument={renderDocument} highlight={["[PERSON_1]"]} />,
        );
        await flush();
        const surface = surfaceOf(view.container);
        expect(jest.getTimerCount()).toBe(1);

        view.unmount();
        expect(jest.getTimerCount()).toBe(0);
        // React detaches refs before passive cleanup, so use the detached
        // surface as a sentinel: no late callback may mutate it any further.
        const afterUnmount = surface.innerHTML;

        await act(async () => {
            jest.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS + 10);
        });
        expect(surface.innerHTML).toBe(afterUnmount);
    });

    it("a newer render leaves a single pending highlight timer", async () => {
        jest.useFakeTimers();
        const renderDocument: DocxRender = async (_blob, container) => {
            container.innerHTML = "<p>[PERSON_1]</p>";
        };

        const view = render(
            <DocxViewer blob={BLOB_A} renderDocument={renderDocument} highlight={["[PERSON_1]"]} />,
        );
        await flush();
        expect(jest.getTimerCount()).toBe(1);

        await act(async () => {
            view.rerender(
                <DocxViewer
                    blob={BLOB_B}
                    renderDocument={renderDocument}
                    highlight={["[PERSON_1]"]}
                />,
            );
        });
        expect(jest.getTimerCount()).toBe(1); // the old pass was cancelled

        await act(async () => {
            jest.advanceTimersByTime(HIGHLIGHT_DEBOUNCE_MS);
        });
        expect(jest.getTimerCount()).toBe(0);
        expect(marks(surfaceOf(view.container))).toEqual(["[PERSON_1]"]);
    });
});
