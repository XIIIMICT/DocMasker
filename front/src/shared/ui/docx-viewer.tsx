"use client";

import { type RefObject, useCallback, useEffect, useRef, useState } from "react";

/** Rapid keys edits must not re-walk the whole rendered document on each keystroke. */
export const HIGHLIGHT_DEBOUNCE_MS = 120;
/** Stable default so an unhighlighted viewer never re-schedules a pass. */
const NO_HIGHLIGHTS: string[] = [];

/**
 * Injectable async renderer. `isCurrent` is the component's generation guard,
 * checked by the default renderer between the dynamic import and `renderAsync`
 * so a stale import never starts an expensive render.
 */
export type DocxRender = (
    blob: Blob,
    container: HTMLElement,
    isCurrent: () => boolean,
) => Promise<void>;

/** Default renderer: the preview bundle is fetched only when a blob is shown. */
const defaultRenderDocument: DocxRender = async (blob, container, isCurrent) => {
    const { renderAsync } = await import("docx-preview");
    if (!isCurrent()) return;
    await renderAsync(blob, container);
};

type TimerRef = { current: ReturnType<typeof setTimeout> | null };

/** Cancel pending highlight work (blob switch, unmount, or a newer keys edit). */
export function cancelHighlightUpdate(timerRef: TimerRef): void {
    if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
    }
}

/**
 * Coalesce re-highlighting: only the last scheduled pass survives the delay.
 * `isCurrent` lets the caller drop work that became stale while it waited
 * (e.g. the generation changed under a pending timer).
 */
export function scheduleHighlightUpdate(
    timerRef: TimerRef,
    apply: () => void,
    isCurrent: () => boolean,
    delayMs: number,
): void {
    cancelHighlightUpdate(timerRef);
    timerRef.current = setTimeout(() => {
        timerRef.current = null;
        if (isCurrent()) apply();
    }, delayMs);
}

/**
 * Renders a .docx Blob into a scrollable pane via the `docx-preview` library.
 * The container is a live document surface: the user can scroll through the
 * rendered pages in the same place a TextArea would occupy.
 *
 * `surfaceRef` lets a parent observe the scrollable element (e.g. to keep two
 * previews in sync); when omitted, the viewer uses its own internal ref.
 *
 * `highlight` is **preview-only**: every occurrence of a listed substring in
 * the rendered text is wrapped in a `<mark>`. The downloaded document is never
 * altered — this just tints the on-screen result. Pass the mask placeholders
 * for a masked preview, or the restored values for a demasked one.
 *
 * `docx-preview` (and its JSZip dependency) is loaded with a dynamic import
 * the first time a document is actually rendered, so a text-only session
 * never downloads the preview bundle. `renderDocument` is an optional seam for
 * deterministic lifecycle tests; production always uses the lazy default.
 */
export function DocxViewer({
    blob,
    surfaceRef: externalRef,
    highlight = NO_HIGHLIGHTS,
    renderDocument = defaultRenderDocument,
}: {
    blob: Blob;
    surfaceRef?: RefObject<HTMLDivElement | null>;
    highlight?: string[];
    renderDocument?: DocxRender;
}) {
    const internalRef = useRef<HTMLDivElement>(null);
    const surfaceRef = externalRef ?? internalRef;
    // Keep the render effect keyed to the blob/surface only: swapping the
    // renderer or the highlight list must never re-parse the document.
    const renderDocumentRef = useRef(renderDocument);
    renderDocumentRef.current = renderDocument;
    const [localError, setLocalError] = useState<string | null>(null);
    // Latest values, read by async callbacks without re-triggering a render.
    const highlightRef = useRef(highlight);
    highlightRef.current = highlight;
    // Whether a document has finished rendering (so highlights can be applied).
    const renderedRef = useRef(false);
    // Generation guard: loading/rendering is async and can resolve after the
    // Blob changed or the component unmounted. Each render/cleanup bumps the
    // generation; a stale pass must not write its DOM or apply highlights.
    const generationRef = useRef(0);
    const lastAppliedRef = useRef(0);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const applyHighlight = useCallback(() => {
        const container = surfaceRef.current;
        if (!container || !renderedRef.current) return;
        if (lastAppliedRef.current !== generationRef.current) return;
        highlightTexts(container, highlightRef.current);
    }, [surfaceRef]);
    const highlightIsCurrent = useCallback(
        () => renderedRef.current && lastAppliedRef.current === generationRef.current,
        [],
    );

    // Render once per document, then apply the current highlight. A change to
    // the highlight list alone re-applies it (debounced) without re-parsing
    // the document.
    const render = useCallback(async () => {
        const container = surfaceRef.current;
        if (!container) return;
        const generation = ++generationRef.current;
        cancelHighlightUpdate(highlightTimerRef);
        container.innerHTML = "";
        setLocalError(null);
        renderedRef.current = false;
        try {
            await renderDocumentRef.current(blob, container, () => {
                // The import itself may outlive a file switch or an unmount.
                return generation === generationRef.current;
            });
            // Ignore a stale render: a newer Blob was selected, or cleanup ran.
            if (generation !== generationRef.current) return;
            renderedRef.current = true;
            lastAppliedRef.current = generation;
            if (highlightRef.current.length > 0) {
                highlightTexts(container, highlightRef.current);
            }
        } catch {
            if (generation !== generationRef.current) return;
            // A failed chunk load surfaces through this component's existing
            // error path — never a silent unhandled rejection.
            setLocalError("Не удалось отобразить предпросмотр документа.");
        }
    }, [blob, surfaceRef]);

    useEffect(() => {
        void render();
        return () => {
            // Invalidate any in-flight import/render before clearing the DOM,
            // and drop highlight work scheduled for this generation.
            generationRef.current += 1;
            renderedRef.current = false;
            cancelHighlightUpdate(highlightTimerRef);
            if (surfaceRef.current) {
                surfaceRef.current.innerHTML = "";
            }
        };
    }, [render, surfaceRef]);

    // Coalesce rapid keys edits, but only once a render exists and only for
    // the generation currently on screen. Cleanup cancels a stale timer.
    useEffect(() => {
        scheduleHighlightUpdate(
            highlightTimerRef,
            applyHighlight,
            highlightIsCurrent,
            HIGHLIGHT_DEBOUNCE_MS,
        );
        return () => cancelHighlightUpdate(highlightTimerRef);
    }, [highlight, surfaceRef, applyHighlight, highlightIsCurrent]);

    return (
        <div className="docx-viewer">
            {localError ? <div className="docx-viewer__error">{localError}</div> : null}
            <div ref={surfaceRef} className="docx-viewer__surface" />
        </div>
    );
}

/** Wrap every occurrence of any `needles` in `root` (preview-only). */
export function highlightTexts(root: HTMLElement, needles: string[]): void {
    // Undo any previous pass first, so re-applying never nests marks.
    for (const mark of Array.from(root.querySelectorAll("mark.docx-marker"))) {
        mark.replaceWith(document.createTextNode(mark.textContent ?? ""));
    }

    const escaped = needles
        .filter((needle) => needle.length > 0)
        .map((needle) => needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (escaped.length === 0) return;
    const pattern = new RegExp(escaped.join("|"), "g");

    // Collect text nodes first: replacing a node invalidates the walker.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes: Text[] = [];
    let node = walker.nextNode();
    while (node) {
        nodes.push(node as Text);
        node = walker.nextNode();
    }

    for (const textNode of nodes) {
        const content = textNode.textContent ?? "";
        const fragment = document.createDocumentFragment();
        let cursor = 0;
        for (const match of content.matchAll(pattern)) {
            const start = match.index ?? 0;
            if (start > cursor) {
                fragment.append(document.createTextNode(content.slice(cursor, start)));
            }
            const mark = document.createElement("mark");
            mark.className = "docx-marker";
            mark.textContent = match[0];
            fragment.append(mark);
            cursor = start + match[0].length;
        }
        if (cursor === 0) continue; // nothing matched
        if (cursor < content.length) {
            fragment.append(document.createTextNode(content.slice(cursor)));
        }
        textNode.replaceWith(fragment);
    }
}
