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
 */
export function DocxViewer({
    blob,
    surfaceRef: externalRef,
}: {
    blob: Blob;
    surfaceRef?: RefObject<HTMLDivElement | null>;
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
        } catch {
            setLocalError("Не удалось отобразить предпросмотр документа.");
        }
    }, [blob, surfaceRef]);

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
