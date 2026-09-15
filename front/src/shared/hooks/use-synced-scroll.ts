"use client";

import { useEffect, type RefObject } from "react";

/**
 * Compute the target `scrollTop` that mirrors `source`'s relative position.
 *
 * The panes rarely have identical content height (masking replaces text with
 * shorter `[LABEL]` markers; DOCX previews paginate independently), so we map
 * the *ratio* of the scroll position rather than the raw pixel offset. Returns
 * `null` when either pane cannot scroll, so callers can skip the update.
 */
export function relativeScrollTop(source: HTMLElement, target: HTMLElement): number | null {
    const sourceRange = source.scrollHeight - source.clientHeight;
    const targetRange = target.scrollHeight - target.clientHeight;
    if (sourceRange <= 0 || targetRange <= 0) return null;
    return (source.scrollTop / sourceRange) * targetRange;
}

/**
 * Keep two scrollable elements scrolled to the same relative position.
 *
 * The listener is attached to both elements; a short re-entrancy guard stops
 * the programmatic scroll on one pane from bouncing back and fighting the
 * user's scroll on the other. `scrollLeft` is mirrored too, for wide documents.
 */
export function useSyncedScroll(
    firstRef: RefObject<HTMLElement | null>,
    secondRef: RefObject<HTMLElement | null>,
    enabled = true,
): void {
    useEffect(() => {
        const first = firstRef.current;
        const second = secondRef.current;
        if (!enabled || !first || !second) return;

        let syncing = false;
        let frame = 0;

        const sync = (source: HTMLElement, target: HTMLElement) => {
            const top = relativeScrollTop(source, target);
            if (top === null) return;
            syncing = true;
            target.scrollTop = top;
            target.scrollLeft = source.scrollLeft;
            cancelAnimationFrame(frame);
            frame = requestAnimationFrame(() => {
                syncing = false;
            });
        };

        const onFirstScroll = () => {
            if (!syncing) sync(first, second);
        };
        const onSecondScroll = () => {
            if (!syncing) sync(second, first);
        };

        first.addEventListener("scroll", onFirstScroll, { passive: true });
        second.addEventListener("scroll", onSecondScroll, { passive: true });
        return () => {
            cancelAnimationFrame(frame);
            first.removeEventListener("scroll", onFirstScroll);
            second.removeEventListener("scroll", onSecondScroll);
        };
    }, [firstRef, secondRef, enabled]);
}
