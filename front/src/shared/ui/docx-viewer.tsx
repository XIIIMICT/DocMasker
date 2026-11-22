"use client";

import { renderAsync } from "docx-preview";
import { type RefObject, useCallback, useEffect, useRef, useState } from "react";

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
 */
export function DocxViewer({
    blob,
    surfaceRef: externalRef,
    highlight = [],
}: {
    blob: Blob;
    surfaceRef?: RefObject<HTMLDivElement | null>;
    highlight?: string[];
}) {
    const internalRef = useRef<HTMLDivElement>(null);
    const surfaceRef = externalRef ?? internalRef;
    const [localError, setLocalError] = useState<string | null>(null);
    // Latest highlight list, read by the render pass without re-triggering it.
    const highlightRef = useRef(highlight);
    highlightRef.current = highlight;
    // Whether a document has finished rendering (so highlights can be applied).
    const renderedRef = useRef(false);

    // Render once per document, then apply the current highlight. A change to
    // the highlight list alone re-applies it without re-parsing the document.
    const render = useCallback(async () => {
        const container = surfaceRef.current;
        if (!container) return;
        container.innerHTML = "";
        setLocalError(null);
        renderedRef.current = false;
        try {
            await renderAsync(blob, container);
            renderedRef.current = true;
            if (highlightRef.current.length > 0) {
                highlightTexts(container, highlightRef.current);
            }
        } catch {
            setLocalError("Не удалось отобразить предпросмотр документа.");
        }
    }, [blob, surfaceRef]);

    useEffect(() => {
        void render();
        return () => {
            if (surfaceRef.current) {
                surfaceRef.current.innerHTML = "";
            }
        };
    }, [render, surfaceRef]);

    // Re-apply highlights when the list changes, but only once a render exists.
    useEffect(() => {
        const container = surfaceRef.current;
        if (!container || !renderedRef.current) return;
        highlightTexts(container, highlight);
    }, [highlight, surfaceRef]);

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
