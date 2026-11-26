"use client";

import { useRef, type ChangeEvent, type DragEvent, type RefObject } from "react";

import type { ModeState } from "@/features/masking/types";
import { fileKind } from "@/features/masking/types";
import type { JobAccepted } from "@/shared/api/jobs";
import type { DragHandlers } from "@/shared/ui/pane-types";

type Update = (patch: Partial<ModeState>) => void;

interface FileInputDeps {
    busy: boolean;
    dragging: boolean;
    inFileMode: boolean;
    activeJob: JobAccepted | null;
    onCancelJob: () => void;
    update: Update;
    resetResult: () => void;
}

/**
 * Owns the hidden file inputs, drag/drop bookkeeping and file-action buttons.
 * DOM refs stay at this UI/workflow boundary; no request or download helper
 * receives them. `.txt` is read client-side into the text input; only `.docx`
 * becomes a selected file for the backend.
 */
export function useFileInput({
    busy,
    dragging,
    inFileMode,
    activeJob,
    onCancelJob,
    update,
    resetResult,
}: FileInputDeps): {
    fileInputRef: RefObject<HTMLInputElement | null>;
    keysInputRef: RefObject<HTMLInputElement | null>;
    drag: DragHandlers;
    onFileSelected: (event: ChangeEvent<HTMLInputElement>) => void;
    onKeysSelected: (event: ChangeEvent<HTMLInputElement>) => void;
    onKeysDrop: (event: DragEvent<HTMLDivElement>) => void;
    onMaskFileActionPress: () => void;
    onDataFileActionPress: () => void;
    onKeysFileActionPress: () => void;
} {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const keysInputRef = useRef<HTMLInputElement>(null);
    // Drag events fire for every child element; count enter/leave to know when
    // the pointer really left the drop zone.
    const dragDepth = useRef(0);

    async function applyFile(file: File) {
        const kind = fileKind(file);
        if (kind === null) {
            update({ error: "Поддерживаются файлы .docx и .txt." });
            return;
        }
        resetResult();
        if (kind === "docx") {
            update({ input: "", textBaseName: null, selectedFile: file });
            return;
        }
        // `.txt`: read its contents into the editable input TextArea (it is not
        // shown as a file — it is just the input text).
        try {
            const text = await file.text();
            update({
                selectedFile: null,
                textBaseName: file.name.replace(/\.txt$/i, ""),
                input: text,
            });
        } catch {
            update({ error: "Не удалось прочитать текстовый файл." });
        }
    }

    function onFileSelected(event: ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;
        void applyFile(file);
    }

    function onDragEnter(event: DragEvent<HTMLDivElement>) {
        if (busy || !event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        dragDepth.current += 1;
        update({ dragging: true });
    }

    function onDragOver(event: DragEvent<HTMLDivElement>) {
        if (busy || !event.dataTransfer.types.includes("Files")) return;
        // Required so the browser fires `drop` instead of opening the file.
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
    }

    function onDragLeave(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) update({ dragging: false });
    }

    function onDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        dragDepth.current = 0;
        update({ dragging: false });
        if (busy) return;
        const file = event.dataTransfer.files?.[0];
        if (!file) return;
        void applyFile(file);
    }

    /** Load a keys file (`.yaml`) into the demasking editor and show its tab. */
    async function applyKeysFile(file: File) {
        if (
            !file.name.toLowerCase().endsWith(".yaml") &&
            !file.name.toLowerCase().endsWith(".yml")
        ) {
            update({ error: "Ключи демаскировки должны быть файлом .yaml." });
            return;
        }
        try {
            update({ demaskKeys: await file.text(), error: null, inputTab: "keys" });
        } catch {
            update({ error: "Не удалось прочитать файл ключей." });
        }
    }

    function onKeysSelected(event: ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (file) void applyKeysFile(file);
    }

    function onKeysDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        const file = event.dataTransfer.files?.[0];
        if (file) void applyKeysFile(file);
    }

    /**
     * The masking and demasking data-file buttons are semantically identical:
     * both cancel an active job, otherwise remove the selected file or open
     * the same hidden picker. They stay two names in the view-model, but share
     * one implementation so the two paths cannot drift apart.
     */
    function onDataFileActionPress() {
        if (activeJob) {
            onCancelJob();
            return;
        }
        if (busy) return;
        if (inFileMode) {
            // Removing the input must also drop the previous result (including
            // the "nothing was masked" notice).
            resetResult();
            update({ selectedFile: null, textBaseName: null });
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        } else {
            fileInputRef.current?.click();
        }
    }

    const drag: DragHandlers = {
        dragging,
        onDragEnter,
        onDragOver,
        onDragLeave,
        onDrop,
    };

    function onKeysFileActionPress() {
        if (busy) return;
        keysInputRef.current?.click();
    }

    return {
        fileInputRef,
        keysInputRef,
        drag,
        onFileSelected,
        onKeysSelected,
        onKeysDrop,
        onMaskFileActionPress: onDataFileActionPress,
        onDataFileActionPress,
        onKeysFileActionPress,
    };
}
