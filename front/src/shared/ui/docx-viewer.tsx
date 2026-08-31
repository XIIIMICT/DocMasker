"use client";

import { renderAsync } from "docx-preview";
import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Renders a .docx Blob into a scrollable pane via the `docx-preview` library.
 * The container is a live document surface: the user can scroll/pfan through
 * the rendered pages in the same place a TextArea would occupy.
 */
export function DocxViewer({ blob }: { blob: Blob }) {
    const containerRef = useRef<HTMLDivElement>(null);
    const [localError, setLocalError] = useState<string | null>(null);

    const clearAndRender = useCallback(async () => {
        const container = containerRef.current;
        if (!container) return;
        container.innerHTML = "";
        setLocalError(null);
        try {
            await renderAsync(blob, container);
        } catch {
            setLocalError("Не удалось отобразить предпросмотр документа.");
        }
    }, [blob]);

    useEffect(() => {
        void clearAndRender();
        return () => {
            if (containerRef.current) {
                containerRef.current.innerHTML = "";
            }
        };
    }, [clearAndRender]);

    return (
        <div className="docx-viewer">
            {localError ? <div className="docx-viewer__error">{localError}</div> : null}
            <div ref={containerRef} className="docx-viewer__surface" />
        </div>
    );
}
