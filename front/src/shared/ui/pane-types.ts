"use client";

import type { DragEvent } from "react";

/**
 * Props that visually disable a `Button` without setting the native `disabled`
 * attribute. react-aria renders `disabled` on the server but resolves it to
 * `undefined` on the client, so `isDisabled` on a conditionally-disabled button
 * trips React's hydration check — `aria-disabled` + a class is stable.
 */
export interface DisabledProps {
    "aria-disabled": boolean;
    /** Absent (not `undefined`) when enabled: HeroUI props reject an explicit undefined. */
    className?: "action-disabled";
}

export function disabledProps(disabled: boolean): DisabledProps {
    return disabled
        ? { "aria-disabled": true, className: "action-disabled" }
        : { "aria-disabled": false };
}

/** Drag-and-drop handlers shared by both input panes. */
export interface DragHandlers {
    dragging: boolean;
    onDragEnter: (event: DragEvent<HTMLDivElement>) => void;
    onDragOver: (event: DragEvent<HTMLDivElement>) => void;
    onDragLeave: (event: DragEvent<HTMLDivElement>) => void;
    onDrop: (event: DragEvent<HTMLDivElement>) => void;
}
