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

    const clearAndRender = useCallback(async () => {
        const container = surfaceRef.current;
        if (!container) return;
        container.innerHTML = "";
        setLocalError(null);
        try {
            await renderAsync(blob, container);
            if (highlight.length > 0) {
                highlightTexts(container, highlight);
            }
        } catch {
            setLocalError("Не удалось отобразить предпросмотр документа.");
        }
    }, [blob, surfaceRef, highlight]);

    useEffect(() => {
        void clearAndRender();
        return () => {
            if (surfaceRef.current) {
                surfaceRef.current.innerHTML = "";
            }
        };
    }, [clearAndRender, surfaceRef]);

    return (
        <div className="docx-viewer">
            {localError ? <div className="docx-viewer__error">{localError}</div> : null}
            <div ref={surfaceRef} className="docx-viewer__surface" />
        </div>
    );
}

/** Wrap every occurrence of any `needles` in `root` (preview-only). */
export function highlightTexts(root: HTMLElement, needles: string[]): void {
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
