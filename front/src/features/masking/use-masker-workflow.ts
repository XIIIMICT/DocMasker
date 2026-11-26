"use client";

import { useEffect, useMemo, useRef } from "react";

import { canDemask } from "@/features/masking/demask-guard";
import {
    operationError,
    postMaskFile,
    postMaskText,
    postUnmaskFile,
    postUnmaskText,
    readErrorDetail,
    resolveMaskBundle,
    resolveMaskResponse,
    resolveRestoreBundle,
    resolveUnmaskResponse,
} from "@/features/masking/mask-requests";
import {
    downloadKeys as saveKeys,
    downloadMaskedFile as saveMaskedFile,
    downloadMaskedText as saveMaskedText,
    downloadRestored as saveRestored,
} from "@/features/masking/result-download";
import { fileKind } from "@/features/masking/types";
import { useFileInput } from "@/features/masking/use-file-input";
import { useJobLifecycle } from "@/features/masking/use-job-lifecycle";
import { useModeDrafts } from "@/features/masking/use-mode-drafts";
import { validateKeysYaml } from "@/shared/api/mask";
import { countPlaceholdersInDocx } from "@/shared/api/placeholder-count";
import { useSyncedScroll } from "@/shared/hooks/use-synced-scroll";
import {
    type MaskStats,
    statsFromAggregate,
    statsFromManifest,
    statsFromParsedKeys,
} from "@/shared/mask-stats";
import { parseKeys } from "@/shared/masking/keys-parse";

/**
 * Composition root for the masking page. Draft ownership lives in
 * `useModeDrafts`, the active job in `useJobLifecycle`, file/DOM input in
 * `useFileInput`, requests in `mask-requests.ts` and downloads in
 * `result-download.ts`. This hook keeps only the derived view-model, scroll
 * wiring and the two operation boundaries; the shape returned here is the
 * contract consumed by `app/masker-page.tsx`.
 */
export function useMaskerWorkflow() {
    const { mode, inMaskMode, state, setStateByMode, update, resetResult, onModeChange } =
        useModeDrafts();
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

    const jobs = useJobLifecycle({ mode, activeJob, update, setStateByMode });
    const { finish, onCancelJob } = jobs;

    const hasText = input.trim().length > 0;
    const hasFile = selectedFile !== null;
    // Exactly one input mode is active; a selected DOCX takes precedence.
    const inFileMode = hasFile;

    const {
        fileInputRef,
        keysInputRef,
        drag,
        onFileSelected,
        onKeysSelected,
        onKeysDrop,
        onMaskFileActionPress,
        onDataFileActionPress,
        onKeysFileActionPress,
    } = useFileInput({
        busy,
        dragging,
        inFileMode,
        activeJob,
        onCancelJob: () => void onCancelJob(),
        update,
        resetResult,
    });

    // Demasking a DOCX: the masked placeholders live inside the file, so
    // count them asynchronously for the tooltip's "с повторами" part. Text
    // mode counts them synchronously from the input string via the shared parse.
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
                if (!active) return;
                // Includes a failed lazy JSZip chunk load: surface it through
                // the same result-pane error path as every other async failure
                // instead of swallowing it into a null count.
                update({
                    demaskOccurrences: null,
                    error: "Не удалось подготовить данные документа.",
                });
            });
        return () => {
            active = false;
        };
    }, [inMaskMode, selectedFile]);

    // Paired scroll panes: original on the left, result on the right. Both text
    // areas and both DOCX previews scroll in lock-step. DOM refs stay here at
    // the UI/workflow boundary.
    const inputTextRef = useRef<HTMLTextAreaElement>(null);
    const resultTextRef = useRef<HTMLTextAreaElement>(null);
    const sourceDocRef = useRef<HTMLDivElement>(null);
    const maskedDocRef = useRef<HTMLDivElement>(null);
    const restoredDocRef = useRef<HTMLDivElement>(null);

    const maskEnabled = !busy && (hasText || hasFile);
    // Demasking needs both an input (text or DOCX) and valid keys. The keys are
    // parsed once per edit and the same result feeds validation, the tooltip
    // counts and (through mask.ts) the preview highlights.
    const parsedDemaskKeys = useMemo(() => parseKeys(demaskKeys), [demaskKeys]);
    const keysError = demaskKeys.trim().length > 0 ? validateKeysYaml(parsedDemaskKeys) : null;
    const keysInvalid = keysError !== null;
    const hasDemaskKeys = demaskKeys.trim().length > 0 && !keysInvalid;
    const unmaskEnabled = canDemask({
        busy,
        hasText,
        hasFile,
        hasKeys: hasDemaskKeys,
        keysError,
    });
    // For demasking, the counts mirror masking; entries whose placeholder is
    // absent from the masked input are reported as "не применено". In file mode
    // the document text is only known to the backend, so the unused list comes
    // from its response (`demaskUnused`); in text mode it is derived live.
    const demaskStats: MaskStats | null = hasDemaskKeys
        ? {
              ...statsFromParsedKeys(
                  parsedDemaskKeys,
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

    function onTextChange(value: string) {
        update({ input: value, selectedFile: null, textBaseName: value ? textBaseName : null });
        resetResult();
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
            const response = await postMaskText(input);
            if (!response.ok) {
                throw new Error(await readErrorDetail(response));
            }
            const data = await resolveMaskResponse(response, jobs);
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
                error: operationError(requestError, "Не удалось выполнить маскирование."),
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
            const response = await postMaskFile(file);
            if (!response.ok) {
                throw new Error(await readErrorDetail(response));
            }
            // The flat ZIP holds the masked .docx, keys and manifest.json.
            const bundle = await resolveMaskBundle(response, jobs);
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
                error: operationError(requestError, "Не удалось обработать файл."),
            });
        } finally {
            finish();
        }
    }

    async function handleUnmask() {
        // Revalidate at the operation boundary: a visual `isDisabled` state is
        // not a guard against Enter/Space or a direct handler invocation.
        if (
            !canDemask({
                busy,
                hasText,
                hasFile,
                hasKeys: hasDemaskKeys,
                keysError,
            })
        ) {
            // Keep the existing UX: a malformed keys string shows the reason,
            // while empty input or a busy state is a silent no-op.
            if (keysError !== null) update({ error: keysError });
            return;
        }
        update({ error: null });

        update({ busy: true });
        try {
            if (inFileMode) {
                const response = await postUnmaskFile(selectedFile!, demaskKeys);
                if (!response.ok) {
                    throw new Error(await readErrorDetail(response));
                }
                // The backend returns a ZIP: restored .docx + a report manifest.
                const bundle = await resolveRestoreBundle(response, jobs);
                update({
                    restoredFile: { blob: bundle.docx, name: bundle.docxName },
                    demaskUnused: bundle.manifest.statistics.unused_keys ?? [],
                });
            } else {
                const response = await postUnmaskText(input, demaskKeys);
                if (!response.ok) {
                    throw new Error(await readErrorDetail(response));
                }
                const data = await resolveUnmaskResponse(response, jobs);
                update({ restored: data.text });
            }
        } catch (requestError) {
            update({
                restored: "",
                restoredFile: null,
                error: operationError(requestError, "Не удалось выполнить демаскирование."),
            });
        } finally {
            finish();
        }
    }

    /** Download the masked artifact, whichever mode produced it. */
    function downloadResult() {
        if (!canDownloadResult) return;
        if (inFileMode) {
            saveMaskedFile(maskedFile);
        } else {
            saveMaskedText(result, textBaseName);
        }
    }

    /** Download the restored (demasked) artifact, text or document. */
    function downloadRestored() {
        if (!canDownloadRestored) return;
        saveRestored(restored, restoredFile, textBaseName);
    }

    function downloadKeys() {
        if (!canDownloadKeys) return;
        saveKeys(keys, maskedFile, textBaseName);
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
        parsedDemaskKeys,
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
