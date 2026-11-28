/**
 * Preview-only highlight pass and its debounce scheduling. Deliberately
 * React-free and independent of `docx-viewer.tsx`: the viewer owns the render
 * lifecycle and generation/staging guards and imports these helpers.
 */

/** Rapid keys edits must not re-walk the whole rendered document on each keystroke. */
export const HIGHLIGHT_DEBOUNCE_MS = 120;

/** Mutable timer slot passed to the coalescing helpers by the viewer. */
export type TimerRef = { current: ReturnType<typeof setTimeout> | null };

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
 * Nearest logical text container. `docx-preview` splits paragraphs into spans
 * and runs; matching must survive those splits but never join two paragraphs.
 */
export const BLOCK_CONTAINER = "p, div, li, td, th, h1, h2, h3, h4, h5, h6, blockquote";

export function containerOf(node: Text, root: HTMLElement): Element {
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
