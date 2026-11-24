/**
 * State and value types for the masking/demasking workflows.
 *
 * Both modes keep independent drafts (`Record<AppMode, ModeState>`), so
 * switching flows never discards either. Extracted from `masker-page.tsx`
 * during the stage-5 frontend decomposition.
 */

import type { JobAccepted } from "@/shared/api/jobs";
import type { MaskStats } from "@/shared/mask-stats";
import type { AppMode } from "@/shared/ui/brand";

/** The two downloadable artifacts of a masking run. */
export interface MaskedFileResult {
    result: Blob;
    resultName: string;
    keys: Blob;
    keysName: string;
}

/** A downloadable document produced by a run (masked or restored). */
export interface DocxResult {
    blob: Blob;
    name: string;
}

export const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const TEXT_MIME = "text/plain";
export const ACCEPTED_EXTENSIONS = [".docx", ".txt"] as const;
/** localStorage key for the last active mode (persisted across reloads). */
export const MODE_STORAGE_KEY = "docmasker.mode";

/**
 * All editable/renderable state of one mode. Keeping every field under its mode
 * is what lets the user switch flows without losing either draft, while the two
 * drafts stay independent.
 */
export interface ModeState {
    input: string;
    selectedFile: File | null;
    textBaseName: string | null;
    // Masking result.
    result: string;
    keys: string;
    stats: MaskStats | null;
    maskedFile: MaskedFileResult | null;
    resultTab: string;
    // Demasking input and result.
    demaskKeys: string;
    inputTab: string;
    restored: string;
    restoredFile: DocxResult | null;
    // Keys the backend reported as unused after a file demasking (a `.docx`
    // text is only known server-side). `null` → derive live from the input.
    demaskUnused: string[] | null;
    // Shared UI state.
    busy: boolean;
    dragging: boolean;
    error: string | null;
    // The 202 body of the job currently being polled (for Cancel / resume).
    activeJob: JobAccepted | null;
}

export function emptyModeState(): ModeState {
    return {
        input: "",
        selectedFile: null,
        textBaseName: null,
        result: "",
        keys: "",
        stats: null,
        maskedFile: null,
        resultTab: "result",
        demaskKeys: "",
        inputTab: "data",
        restored: "",
        restoredFile: null,
        demaskUnused: null,
        busy: false,
        dragging: false,
        error: null,
        activeJob: null,
    };
}

export type FileKind = "docx" | "txt";

export function fileKind(file: File): FileKind | null {
    const name = file.name.toLowerCase();
    if (name.endsWith(".docx") || file.type === DOCX_MIME) return "docx";
    if (name.endsWith(".txt") || file.type === TEXT_MIME) return "txt";
    return null;
}

/** Per-mode state container used by the workflow hook. */
export type StateByMode = Record<AppMode, ModeState>;

export function emptyStateByMode(): StateByMode {
    return { mask: emptyModeState(), unmask: emptyModeState() };
}
