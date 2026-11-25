"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";

import {
    emptyModeState,
    fileKind,
    MODE_STORAGE_KEY,
    type ModeState,
} from "@/features/masking/types";
import { ACTIVE_JOB_KEY, cancelJob, type JobAccepted, waitForJob } from "@/shared/api/jobs";
import { type MaskResponse, type UnmaskResponse, validateKeysYaml } from "@/shared/api/mask";
import { unpackMaskArchive, unpackRestoreArchive } from "@/shared/api/mask-archive";
import {
    jobBundle,
    jobRestoreBundle,
    jobToMaskResponse,
    jobToUnmaskResponse,
} from "@/shared/api/mask-helpers";
import { countPlaceholdersInDocx } from "@/shared/api/placeholder-count";
import { useSyncedScroll } from "@/shared/hooks/use-synced-scroll";
import {
    countKeysValues,
    type MaskStats,
    statsFromAggregate,
    statsFromManifest,
} from "@/shared/mask-stats";
import type { AppMode } from "@/shared/ui/brand";
import type { DragHandlers } from "@/shared/ui/panes";

export function useMaskerWorkflow() {
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

    // --- Per-mode state -------------------------------------------------------
    // Every editable/renderable field lives under the mode that owns it, so
    // switching flows shows the other draft without discarding this one. The
    // active slice is projected onto flat names for the render below; the
    // per-mode slices keep masking and demasking data genuinely separate.
    const [stateByMode, setStateByMode] = useState<Record<AppMode, ModeState>>(() => ({
        mask: emptyModeState(),
        unmask: emptyModeState(),
    }));
    const state = stateByMode[mode];
    const update = (patch: Partial<ModeState>) =>
        setStateByMode((current) => ({ ...current, [mode]: { ...current[mode], ...patch } }));

    /** Remember the active job so Cancel and reload-resume can find it. */
    async function trackAccepted(response: Response): Promise<JobAccepted> {
        const accepted = (await response.json()) as JobAccepted;
        update({ activeJob: accepted });
        try {
            window.sessionStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ mode, accepted }));
        } catch {
            /* private mode: in-memory state still supports Cancel this session */
        }
        return accepted;
    }

    /** Drop the active job after a terminal state or explicit dismissal. */
    function endJob() {
        update({ activeJob: null });
        try {
            window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
        } catch {
            /* ignore */
        }
    }

    /** Release polling state after any run (success, failure or cancellation). */
    function finish() {
        pollingController.current = null;
        endJob();
        update({ busy: false });
    }

    /** Cancel the server-side job (AbortController only stops polling). */
    async function onCancelJob() {
        if (!activeJob) return;
        await cancelJob(activeJob);
        pollingController.current?.abort();
        endJob();
        update({ busy: false, error: "Задание отменено." });
    }

    const pollingController = useRef<AbortController | null>(null);

    const {
        input,
        selectedFile,
        textBaseName,
        result,
        keys,
        stats,
        maskedFile,
        demaskKeys,
        restored,
        restoredFile,
        demaskUnused,
        demaskOccurrences,
        inputTab,
        resultTab,
        busy,
        error,
        activeJob,
    } = state;
    const dragging = state.dragging;

    // Demasking a DOCX: the masked placeholders live inside the file, so
    // count them asynchronously for the tooltip's "с повторами" part. Text
    // mode counts them synchronously from the input string in countKeysValues.
    useEffect(() => {
        if (inMaskMode || selectedFile === null || fileKind(selectedFile) !== "docx") {
            if (demaskOccurrences !== null) update({ demaskOccurrences: null });
            return;
        }
        let active = true;
        void countPlaceholdersInDocx(selectedFile)
            .then((counts) => {
                if (active) update({ demaskOccurrences: counts });
            })
            .catch(() => {
                if (active) update({ demaskOccurrences: null });
            });
        return () => {
            active = false;
        };
    }, [inMaskMode, selectedFile]);

    const fileInputRef = useRef<HTMLInputElement>(null);
    const keysInputRef = useRef<HTMLInputElement>(null);
    // Drag events fire for every child element; count enter/leave to know when
    // the pointer really left the drop zone.
    const dragDepth = useRef(0);
    // Paired scroll panes: original on the left, result on the right. Both text
    // areas and both DOCX previews scroll in lock-step.
    const inputTextRef = useRef<HTMLTextAreaElement>(null);
    const resultTextRef = useRef<HTMLTextAreaElement>(null);
    const sourceDocRef = useRef<HTMLDivElement>(null);
    const maskedDocRef = useRef<HTMLDivElement>(null);
    const restoredDocRef = useRef<HTMLDivElement>(null);

    // Resume an active job after a reload in the same tab (§3.11). Only the
    // opaque job id lives in sessionStorage — never input documents, keys or
    // PII. A 404/expired/failed/cancelled job is handled by waitForJob.
    useEffect(() => {
        let stored: { mode: AppMode; accepted: JobAccepted } | null = null;
        try {
            const raw = window.sessionStorage.getItem(ACTIVE_JOB_KEY);
            if (raw) stored = JSON.parse(raw) as { mode: AppMode; accepted: JobAccepted };
        } catch {
            /* ignore malformed/private-mode storage */
        }
        if (!stored) return;
        const { mode: jobMode, accepted } = stored;
        setStateByMode((current) => ({
            ...current,
            [jobMode]: { ...current[jobMode], activeJob: accepted, busy: true },
        }));
        const controller = new AbortController();
        void waitForJob(accepted, controller.signal)
            .then(async (result) => {
                // File jobs return a ZIP without result.json; resume can only
                // repopulate text results — clear the active job and let the
                // user download from the result URL if they need the file.
                const jsonEntry = result.entry(".json");
                if (!jsonEntry) {
                    setStateByMode((current) => ({
                        ...current,
                        [jobMode]: {
                            ...current[jobMode],
                            activeJob: null,
                            busy: false,
                            error: "Задание завершено. Повторите операцию, чтобы скачать файл.",
                        },
                    }));
                    try {
                        window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                    } catch {
                        /* ignore */
                    }
                    return;
                }
                const payload = JSON.parse(await jsonEntry.text()) as {
                    text?: string;
                    keys?: string;
                    stats?: MaskResponse["stats"];
                    kind?: string;
                };
                setStateByMode((current) => {
                    const slice = { ...current[jobMode], activeJob: null, busy: false };
                    if (jobMode === "mask" && payload.stats !== undefined) {
                        return {
                            ...current,
                            [jobMode]: {
                                ...slice,
                                result: payload.text ?? "",
                                keys: payload.keys ?? "",
                                stats: statsFromAggregate(payload.stats),
                            },
                        };
                    }
                    return { ...current, [jobMode]: { ...slice, restored: payload.text ?? "" } };
                });
                try {
                    window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                } catch {
                    /* ignore */
                }
            })
            .catch((error: unknown) => {
                setStateByMode((current) => ({
                    ...current,
                    [jobMode]: {
                        ...current[jobMode],
                        activeJob: null,
                        busy: false,
                        error:
                            error instanceof Error
                                ? error.message
                                : "Не удалось обработать задание.",
                    },
                }));
                try {
                    window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                } catch {
                    /* ignore */
                }
            });
        return () => controller.abort();
    }, []);

    const hasText = input.trim().length > 0;
    const hasFile = selectedFile !== null;
    // Exactly one input mode is active; a selected DOCX takes precedence.
    const inFileMode = hasFile;
    const maskEnabled = !busy && (hasText || hasFile);
    // Demasking needs both an input (text or DOCX) and valid keys. The keys are
    // validated on the client so an invalid file never reaches the backend; the
    // editor is marked invalid while non-empty and malformed.
    const keysError = demaskKeys.trim().length > 0 ? validateKeysYaml(demaskKeys) : null;
    const keysInvalid = keysError !== null;
    const hasDemaskKeys = demaskKeys.trim().length > 0 && !keysInvalid;
    const unmaskEnabled = !busy && (hasText || hasFile) && hasDemaskKeys;
    // For demasking, the counts mirror masking; entries whose placeholder is
    // absent from the masked input are reported as "не применено". In file mode
    // the document text is only known to the backend, so the unused list comes
    // from its response (`demaskUnused`); in text mode it is derived live.
    const demaskStats: MaskStats | null = hasDemaskKeys
        ? {
              ...countKeysValues(
                  demaskKeys,
                  inFileMode ? null : input,
                  inFileMode ? (demaskOccurrences ?? undefined) : undefined,
              ),
              ...(inFileMode && demaskUnused !== null ? { unused: demaskUnused } : {}),
          }
        : null;
    // An error always surfaces in the result pane, so the result is a TextArea
    // (never a document preview) while one is shown.
    const showResultPreview = inFileMode && maskedFile !== null && error === null;
    // The demasking result is a document when a `.docx` was demasked.
    const showRestoredPreview = inFileMode && restoredFile !== null && error === null;
    // A run happened and masked nothing: the result pane explains it and the
    // keys tab / downloads stay disabled.
    const maskedNothing = error === null && stats !== null && stats.total === 0;
    // The result tab holds the masked artifact once a run has produced one.
    const canDownloadResult =
        error === null && !maskedNothing && (inFileMode ? maskedFile !== null : result.length > 0);
    const canDownloadKeys = error === null && keys.length > 0;
    // The demasking result can be downloaded once a run produced one.
    const canDownloadRestored =
        error === null && (inFileMode ? restoredFile !== null : restored.length > 0);

    useSyncedScroll(inputTextRef, resultTextRef, !inFileMode && resultTab === "result");
    useSyncedScroll(sourceDocRef, maskedDocRef, showResultPreview && resultTab === "result");
    useSyncedScroll(sourceDocRef, restoredDocRef, showRestoredPreview && inputTab === "data");

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

    function onTextChange(value: string) {
        update({ input: value, selectedFile: null, textBaseName: value ? textBaseName : null });
        resetResult();
    }

    function onFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;
        void applyFile(file);
    }

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

    async function handleMask() {
        if (busy) return;
        update({ error: null });
        if (!hasText && !hasFile) return;

        if (hasFile) {
            await handleFileMask();
        } else {
            await handleTextMask();
        }
    }

    async function handleTextMask() {
        update({ busy: true });
        try {
            const response = await fetch("/api/mask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: input }),
            });
            if (!response.ok) {
                throw new Error(await readErrorDetail(response));
            }
            const controller = new AbortController();
            pollingController.current = controller;
            let data: MaskResponse;
            if (response.status === 202) {
                const accepted = await trackAccepted(response);
                data = await jobToMaskResponse(accepted, controller.signal);
                endJob();
            } else {
                data = (await response.json()) as MaskResponse;
            }
            update({
                result: data.text,
                keys: data.keys,
                stats: statsFromAggregate(data.stats),
            });
        } catch (requestError) {
            update({
                result: "",
                keys: "",
                stats: null,
                error:
                    requestError instanceof Error
                        ? requestError.message
                        : "Не удалось выполнить маскирование.",
            });
        } finally {
            finish();
        }
    }

    async function handleFileMask() {
        // Use the selected file from state so a dropped file (which never lands
        // in the hidden input) masks just like a picked one.
        const file = selectedFile;
        if (!file) return;
        update({ busy: true });
        try {
            const formData = new FormData();
            formData.append("file", file, file.name);
            const response = await fetch("/api/mask-file", {
                method: "POST",
                body: formData,
            });
            if (!response.ok) {
                throw new Error(await readErrorDetail(response));
            }
            let bundle: Awaited<ReturnType<typeof unpackMaskArchive>>;
            if (response.status === 202) {
                const accepted = await trackAccepted(response);
                const controller = new AbortController();
                pollingController.current = controller;
                const jobResult = await waitForJob(accepted, controller.signal);
                endJob();
                bundle = await jobBundle(jobResult);
            } else {
                // The flat ZIP holds the masked .docx, keys and manifest.json.
                bundle = await unpackMaskArchive(await response.blob());
            }
            const keysYaml = await bundle.keys.text();
            update({
                maskedFile: {
                    result: bundle.result,
                    resultName: bundle.resultName,
                    keys: bundle.keys,
                    keysName: bundle.keysName,
                },
                keys: keysYaml,
                stats: statsFromManifest(bundle.manifest),
            });
        } catch (requestError) {
            update({
                maskedFile: null,
                keys: "",
                stats: null,
                error:
                    requestError instanceof Error
                        ? requestError.message
                        : "Не удалось обработать файл.",
            });
        } finally {
            finish();
        }
    }

    async function handleUnmask() {
        if (busy) return;
        update({ error: null });
        if (!hasText && !hasFile) return;
        // Never send malformed keys to the backend; the editor shows the reason.
        if (keysError !== null) {
            update({ error: keysError });
            return;
        }

        update({ busy: true });
        try {
            if (inFileMode) {
                const response = await requestFileUnmask();
                if (!response.ok) {
                    throw new Error(await readErrorDetail(response));
                }
                // The backend returns a ZIP: restored .docx + a report manifest.
                let bundle: Awaited<ReturnType<typeof unpackRestoreArchive>>;
                if (response.status === 202) {
                    const accepted = await trackAccepted(response);
                    const controller = new AbortController();
                    pollingController.current = controller;
                    bundle = await jobRestoreBundle(accepted, controller.signal);
                    endJob();
                } else {
                    bundle = await unpackRestoreArchive(await response.blob());
                }
                update({
                    restoredFile: { blob: bundle.docx, name: bundle.docxName },
                    demaskUnused: bundle.manifest.statistics.unused_keys ?? [],
                });
            } else {
                const response = await requestTextUnmask();
                if (!response.ok) {
                    throw new Error(await readErrorDetail(response));
                }
                let data: UnmaskResponse;
                if (response.status === 202) {
                    const accepted = await trackAccepted(response);
                    const controller = new AbortController();
                    pollingController.current = controller;
                    data = await jobToUnmaskResponse(accepted, controller.signal);
                    endJob();
                } else {
                    data = (await response.json()) as UnmaskResponse;
                }
                update({ restored: data.text });
            }
        } catch (requestError) {
            update({
                restored: "",
                restoredFile: null,
                error:
                    requestError instanceof Error
                        ? requestError.message
                        : "Не удалось выполнить демаскирование.",
            });
        } finally {
            finish();
        }
    }

    /** Extract a Russian error `detail` from a failed proxied response. */
    async function readErrorDetail(response: Response): Promise<string> {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        return payload?.detail ?? `Ошибка сервера (${response.status}).`;
    }

    async function requestTextUnmask(): Promise<Response> {
        return fetch("/api/unmask", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: input, keys: demaskKeys }),
        });
    }

    async function requestFileUnmask(): Promise<Response> {
        const file = selectedFile;
        if (!file) throw new Error("Файл не выбран.");
        const formData = new FormData();
        formData.append("file", file, file.name);
        formData.append("keys", demaskKeys);
        return fetch("/api/unmask-file", { method: "POST", body: formData });
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

    function onKeysSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (file) void applyKeysFile(file);
    }

    function onKeysDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        const file = event.dataTransfer.files?.[0];
        if (file) void applyKeysFile(file);
    }

    function downloadBlob(blob: Blob, name: string) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = name;
        anchor.click();
        URL.revokeObjectURL(url);
    }

    function downloadMaskedFile() {
        if (!maskedFile) return;
        downloadBlob(maskedFile.result, maskedFile.resultName);
    }

    function downloadMaskedText() {
        if (result.length === 0) return;
        const base = textBaseName ?? "текст";
        downloadBlob(
            new Blob([result], { type: "text/plain;charset=utf-8" }),
            `${base} (замаскированный).txt`,
        );
    }

    /** Download the masked artifact, whichever mode produced it. */
    function downloadResult() {
        if (inFileMode) {
            downloadMaskedFile();
        } else {
            downloadMaskedText();
        }
    }

    /** Download the restored (demasked) artifact, text or document. */
    function downloadRestored() {
        if (restoredFile) {
            downloadBlob(restoredFile.blob, restoredFile.name);
            return;
        }
        if (restored.length === 0) return;
        const base = textBaseName ?? "текст";
        downloadBlob(
            new Blob([restored], { type: "text/plain;charset=utf-8" }),
            `${base} (демаскированный).txt`,
        );
    }

    /** Keys download: in file mode the name comes from the archive, else derived. */
    function downloadKeys() {
        if (keys.length === 0) return;
        if (maskedFile) {
            downloadBlob(maskedFile.keys, maskedFile.keysName);
            return;
        }
        const base = textBaseName ?? "текст";
        downloadBlob(
            new Blob([keys], { type: "application/yaml;charset=utf-8" }),
            `${base} (ключи демаскировки).yaml`,
        );
    }

    /** Drag-and-drop props shared by the input panes (both modes). */
    const drag: DragHandlers = { dragging, onDragEnter, onDragOver, onDragLeave, onDrop };

    /** Masking file button: Cancel an active job, else remove/pick a file. */
    function onMaskFileActionPress() {
        if (activeJob) {
            void onCancelJob();
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

    /** Demasking data-file button: Cancel an active job, else remove/pick. */
    function onDataFileActionPress() {
        if (activeJob) {
            void onCancelJob();
            return;
        }
        if (busy) return;
        if (inFileMode) {
            resetResult();
            update({ selectedFile: null, textBaseName: null });
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        } else {
            fileInputRef.current?.click();
        }
    }

    /** Demasking keys-file button: load `.yaml`, or open the picker. */
    function onKeysFileActionPress() {
        if (busy) return;
        keysInputRef.current?.click();
    }

    return {
        mode,
        inMaskMode,
        onModeChange,
        input,
        selectedFile,
        inFileMode,
        busy,
        activeJob,
        maskEnabled,
        unmaskEnabled,
        keysInvalid,
        demaskKeys,
        inputTab,
        demaskStats,
        drag,
        onTextChange,
        onMaskFileActionPress,
        onDataFileActionPress,
        onKeysFileActionPress,
        onCancelJob,
        handleMask,
        handleUnmask,
        update,
        onKeysDrop,
        error,
        resultTab,
        stats,
        keys,
        maskedNothing,
        showResultPreview,
        maskedFile,
        result,
        restored,
        restoredFile,
        canDownloadResult,
        canDownloadKeys,
        canDownloadRestored,
        downloadResult,
        downloadKeys,
        downloadRestored,
        inputTextRef,
        resultTextRef,
        sourceDocRef,
        maskedDocRef,
        restoredDocRef,
        fileInputRef,
        keysInputRef,
        onFileSelected,
        onKeysSelected,
    };
}
