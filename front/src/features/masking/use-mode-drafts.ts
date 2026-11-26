"use client";

import { useEffect, useState } from "react";

import { emptyStateByMode, MODE_STORAGE_KEY, type ModeState } from "@/features/masking/types";
import type { AppMode } from "@/shared/ui/brand";

type StateByMode = Record<AppMode, ModeState>;

/**
 * The single owner of both mode drafts. Switching modes swaps the visible
 * slice without clearing either one. `update` closes over the mode of the
 * render that created it, so an operation already in flight still writes its
 * result to the mode where the user started it (§ operation-mode capture).
 */
export function useModeDrafts() {
    const [mode, setMode] = useState<AppMode>("mask");
    const inMaskMode = mode === "mask";

    // Restore the last mode after hydration (localStorage is client-only; an
    // SSR read would disagree with the server and break hydration). The title
    // is derived from the *effective* mode in the same pass, so a restored
    // demasking session can never be paired with the mask-mode title.
    useEffect(() => {
        let effective: AppMode = mode;
        try {
            const stored = window.localStorage.getItem(MODE_STORAGE_KEY);
            if (stored === "unmask") effective = "unmask";
        } catch {
            /* private mode / storage blocked: keep the default */
        }
        if (effective !== mode) setMode(effective);
        document.title = `ДокМаскер / ${effective === "mask" ? "Маскировка" : "Демаскировка"}`;
    }, [mode]);

    const [stateByMode, setStateByMode] = useState<StateByMode>(emptyStateByMode);
    const state = stateByMode[mode];
    const update = (patch: Partial<ModeState>) =>
        setStateByMode((current) => ({ ...current, [mode]: { ...current[mode], ...patch } }));

    /** Clear the *current* mode's result, leaving its input untouched. */
    function resetResult() {
        update({
            result: "",
            keys: "",
            stats: null,
            maskedFile: null,
            restored: "",
            restoredFile: null,
            demaskUnused: null,
            error: null,
            resultTab: "result",
            inputTab: "data",
        });
    }

    // Switching flows keeps both drafts; nothing is cleared here. Remember the
    // choice so a reload reopens the mode the user was working in.
    function onModeChange(next: AppMode) {
        setMode(next);
        try {
            window.localStorage.setItem(MODE_STORAGE_KEY, next);
        } catch {
            /* private mode / storage blocked: the mode still switches this session */
        }
    }

    return {
        mode,
        inMaskMode,
        state,
        stateByMode,
        setStateByMode,
        update,
        resetResult,
        onModeChange,
    };
}

export type { StateByMode };
