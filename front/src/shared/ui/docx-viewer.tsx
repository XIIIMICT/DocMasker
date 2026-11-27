"use client";

import { type RefObject, useCallback, useEffect, useRef, useState } from "react";

/** Rapid keys edits must not re-walk the whole rendered document on each keystroke. */
export const HIGHLIGHT_DEBOUNCE_MS = 120;
/** Stable default so an unhighlighted viewer never re-schedules a pass. */
const NO_HIGHLIGHTS: string[] = [];
const RENDER_ERROR = "Не удалось отобразить предпросмотр документа.";

/**
 * Injectable async renderer. `container` is a generation-private staging host:
 * `docx-preview` writes there, and only the current generation's host is
 * committed to the visible surface. `isCurrent` is checked by the default
 * renderer between the dynamic import and `renderAsync`, so a stale import
 * never starts an expensive render even if the component's own guard is later
 * bypassed by a test double.
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
 *
 * Each generation renders into its own detached staging host. Only a still
 * current generation moves those nodes into the visible surface; an already
 * started `renderAsync` therefore cannot overwrite a newer Blob, even if it
 * finishes late. Staging hosts are dropped on switch, unmount, success and
 * failure, so no obsolete DOM, timer or React state survives a generation.
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
    // generation; a stale pass must not commit its DOM or apply highlights.
    const generationRef = useRef(0);
    const lastAppliedRef = useRef(0);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // One detached staging host per in-flight generation, owned by that
    // generation alone. Never appended to the document or to the surface.
    const stagingRef = useRef<Map<number, HTMLDivElement>>(new Map());
    // React detaches refs before passive cleanup on unmount; keep the element
    // so cleanup can still clear the surface it rendered into.
    const surfaceElementRef = useRef<HTMLDivElement | null>(null);

    /**
     * Clear one generation's host. The explicit reference matters: a renderer
     * that ignores the guard may keep writing into a host already removed from
     * the map, so a late completion must clear the very host it was given.
     */
    const discardStaging = useCallback((generation: number, staging?: HTMLDivElement) => {
        const host = staging ?? stagingRef.current.get(generation);
        if (host) host.replaceChildren();
        if (stagingRef.current.get(generation) === host) {
            stagingRef.current.delete(generation);
        }
    }, []);

    /** Remove every staging host except the one owned by the current generation. */
    const discardObsoleteStaging = useCallback(() => {
        const current = generationRef.current;
        for (const [generation, staging] of stagingRef.current) {
            if (generation === current) continue;
            staging.replaceChildren();
            stagingRef.current.delete(generation);
        }
    }, []);

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

    // Render once per document into a private host, then commit it only if
    // still current. A highlight change re-applies marks in place without
    // re-parsing the document or re-rendering this component.
    const render = useCallback(async () => {
        const surface = surfaceRef.current;
        if (!surface) return;
        const generation = ++generationRef.current;
        surfaceElementRef.current = surface;
        let staging: HTMLDivElement | null = null;
        try {
            cancelHighlightUpdate(highlightTimerRef);
            // The previous document disappears synchronously on a blob switch.
            surface.replaceChildren();
            discardObsoleteStaging();
            setLocalError(null);
            renderedRef.current = false;

            // docx-preview 0.4.1 renders into memory and only clears/appends
            // into the supplied container at the end of `renderAsync`; it
            // never measures that container (the size/computed-style path is
            // experimental and reads document.body). A detached host is
            // therefore sufficient and keeps stale writes off the surface.
            staging = document.createElement("div");
            stagingRef.current.set(generation, staging);
            await renderDocumentRef.current(
                blob,
                staging,
                () => generation === generationRef.current,
            );
            if (generation !== generationRef.current) {
                discardStaging(generation, staging);
                return;
            }
            // Move the nodes out of staging; the host itself never becomes
            // the surface, so an obsolete cleanup cannot remove committed DOM.
            surface.replaceChildren(...Array.from(staging.childNodes));
            discardStaging(generation, staging);
            renderedRef.current = true;
            lastAppliedRef.current = generation;
            if (highlightRef.current.length > 0) {
                highlightTexts(surface, highlightRef.current);
            }
        } catch {
            discardStaging(generation, staging ?? undefined);
            if (generation !== generationRef.current) return;
            // A failed chunk load or render surfaces through the existing
            // error path — never a silent unhandled rejection.
            setLocalError(RENDER_ERROR);
        }
    }, [blob, surfaceRef, discardStaging, discardObsoleteStaging]);

    useEffect(() => {
        void render();
        const ownedSurface = surfaceElementRef.current;
        return () => {
            // Invalidate in-flight work before discarding anything. Clear only
            // this generation's own surface: a newer render may already own
            // the visible DOM, and its committed nodes must survive.
            generationRef.current += 1;
            renderedRef.current = false;
            cancelHighlightUpdate(highlightTimerRef);
            discardObsoleteStaging();
            if (ownedSurface && surfaceElementRef.current === ownedSurface) {
                ownedSurface.replaceChildren();
            }
        };
    }, [render, surfaceRef, discardObsoleteStaging]);

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

/**
 * Nearest logical text container. `docx-preview` splits paragraphs into spans
 * and runs; matching must survive those splits but never join two paragraphs.
 */
const BLOCK_CONTAINER = "p, div, li, td, th, h1, h2, h3, h4, h5, h6, blockquote";

function containerOf(node: Text, root: HTMLElement): Element {
    const nearest = node.parentElement?.closest<HTMLElement>(BLOCK_CONTAINER);
    return nearest && root.contains(nearest) ? nearest : root;
}

/**
 * Wrap every occurrence of any `needles` in `root` (preview-only).
 *
 * Overlap rule (durable, mirrors `back/src/masking/mapping.py`): scanning starts
 * at the earliest position, and at that position the LONGEST matching needle
 * wins. Keys order never changes which value is highlighted. Needles are
 * literal strings, never regex patterns; empty needles are ignored.
 *
 * Consecutive text nodes inside one logical container are matched as one
 * string, so a placeholder split across rendered runs is still highlighted.
 * Each node slice gets its own `<mark>`; a re-application first removes all
 * previous marks, so no mark is ever nested or duplicated.
 */
export function highlightTexts(root: HTMLElement, needles: string[]): void {
    // Undo any previous pass first, so re-applying never nests marks.
    for (const mark of Array.from(root.querySelectorAll("mark.docx-marker"))) {
        mark.replaceWith(document.createTextNode(mark.textContent ?? ""));
    }

    // Longest-first alternation: JS regex alternatives are tried in order, so
    // a shorter needle listed earlier must not shadow a longer one. Dedup keeps
    // the compiled pattern proportional to distinct keys, not key edits.
    const escaped = Array.from(new Set(needles.filter((needle) => needle.length > 0)))
        .sort((a, b) => b.length - a.length)
        .map((needle) => needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (escaped.length === 0) return;
    const pattern = new RegExp(escaped.join("|"), "g");
    pattern.lastIndex = 0;

    // Collect text nodes first: replacing a node invalidates the walker.
    // Consecutive nodes with the same nearest block form one search scope;
    // changing container starts a new scope (no cross-paragraph matches).
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const scopes: Array<{ container: Element; nodes: Text[] }> = [];
    // Runs share a parent element (one span per w:t); cache the ancestor walk.
    const parentContainers = new WeakMap<Element, Element>();
    let scope: { container: Element; nodes: Text[] } | null = null;
    let node = walker.nextNode();
    while (node) {
        const textNode = node as Text;
        const parent = textNode.parentElement;
        let container = parent ? parentContainers.get(parent) : undefined;
        if (!container) {
            container = containerOf(textNode, root);
            if (parent) parentContainers.set(parent, container);
        }
        if (scope === null || scope.container !== container) {
            scope = { container, nodes: [] };
            scopes.push(scope);
        }
        scope.nodes.push(textNode);
        node = walker.nextNode();
    }

    for (const { nodes } of scopes) {
        const joined = nodes.map((textNode) => textNode.data).join("");
        // `matchAll` is lazy: most paragraphs contain no keys and never pay for
        // offset records or a mark map. One regex scan per scope, not two.
        pattern.lastIndex = 0;
        const matches = joined.matchAll(pattern);
        const first = matches.next();
        if (first.done) continue;

        const offsets: Array<{ node: Text; start: number; end: number }> = [];
        let offset = 0;
        for (const textNode of nodes) {
            const length = textNode.data.length;
            offsets.push({ node: textNode, start: offset, end: offset + length });
            offset += length;
        }

        // Map each container-level match back to the text nodes it covers.
        // Matches are ordered and non-overlapping, so one moving cursor walks
        // the offsets once per scope (O(nodes + matches)).
        const rangesByNode = new Map<Text, Array<[number, number]>>();
        let cursor = 0;
        const addMatch = (match: RegExpMatchArray): void => {
            const start = match.index ?? 0;
            const end = start + match[0].length;
            while (cursor < offsets.length && offsets[cursor]!.end <= start) cursor += 1;
            for (let i = cursor; i < offsets.length && offsets[i]!.start < end; i += 1) {
                const offset = offsets[i]!;
                const from = Math.max(start, offset.start) - offset.start;
                const to = Math.min(end, offset.end) - offset.start;
                if (from >= to) continue;
                let ranges = rangesByNode.get(offset.node);
                if (!ranges) rangesByNode.set(offset.node, (ranges = []));
                ranges.push([from, to]);
            }
        };
        addMatch(first.value);
        for (const match of matches) addMatch(match);

        for (const [textNode, ranges] of rangesByNode) {
            const content = textNode.data;
            const fragment = document.createDocumentFragment();
            let at = 0;
            for (const [from, to] of ranges) {
                if (from > at) fragment.append(document.createTextNode(content.slice(at, from)));
                const mark = document.createElement("mark");
                mark.className = "docx-marker";
                mark.textContent = content.slice(from, to);
                fragment.append(mark);
                at = to;
            }
            if (at < content.length) {
                fragment.append(document.createTextNode(content.slice(at)));
            }
            textNode.replaceWith(fragment);
        }
    }
}
