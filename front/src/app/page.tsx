"use client";

import { Button, ButtonGroup, Tabs, TextArea, Tooltip } from "@heroui/react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type RefObject } from "react";

import {
    keyEntries,
    type MaskResponse,
    placeholdersFromKeys,
    type UnmaskResponse,
    validateKeysYaml,
    valuesFromKeys,
} from "@/shared/api/mask";
import { unpackMaskArchive, unpackRestoreArchive } from "@/shared/api/mask-archive";
import { useSyncedScroll } from "@/shared/hooks/use-synced-scroll";
import {
    countKeysValues,
    type MaskStats,
    statsBreakdown,
    statsFromAggregate,
    totalText,
} from "@/shared/mask-stats";
import { Brand, type AppMode } from "@/shared/ui/brand";
import { DocxViewer } from "@/shared/ui/docx-viewer";
import { ThemeSwitcher } from "@/shared/ui/theme-switcher";

/** The two downloadable artifacts of a masking run. */
interface MaskedFileResult {
    result: Blob;
    resultName: string;
    keys: Blob;
    keysName: string;
}

/** A downloadable document produced by a run (masked or restored). */
interface DocxResult {
    blob: Blob;
    name: string;
}

const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const TEXT_MIME = "text/plain";
const ACCEPTED_EXTENSIONS = [".docx", ".txt"];

/**
 * All editable/renderable state of one mode. Keeping every field under its mode
 * is what lets the user switch flows without losing either draft, while the two
 * drafts stay independent.
 */
interface ModeState {
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
}

function emptyModeState(): ModeState {
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
    };
}

type FileKind = "docx" | "txt";

function fileKind(file: File): FileKind | null {
    const name = file.name.toLowerCase();
    if (name.endsWith(".docx") || file.type === DOCX_MIME) return "docx";
    if (name.endsWith(".txt") || file.type === TEXT_MIME) return "txt";
    return null;
}

/** Parse the backend's `X-Masked-Breakdown` header (a JSON label→count map). */
function parseBreakdown(header: string | null): Record<string, number> {
    if (!header) return {};
    try {
        const parsed = JSON.parse(header) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            return parsed as Record<string, number>;
        }
    } catch {
        /* malformed header — show no breakdown */
    }
    return {};
}

function DocIcon() {
    return (
        <svg viewBox="0 0 24 24" width="44" height="44" fill="none" aria-hidden="true">
            <path
                d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5z"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinejoin="round"
            />
            <path d="M14 2v5h5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
            <path
                d="M9 13h6M9 17h6M9 9h1"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
            />
        </svg>
    );
}

/**
 * Props that visually disable a `Button` without setting the native `disabled`
 * attribute. react-aria renders `disabled` on the server but resolves it to
 * `undefined` on the client, so `isDisabled` on a conditionally-disabled button
 * trips React's hydration check — `aria-disabled` + a class is stable.
 */
function disabledProps(disabled: boolean) {
    return {
        "aria-disabled": disabled,
        className: disabled ? "action-disabled" : undefined,
    } as const;
}

function DocPreviewShell({
    title,
    subtitle,
    children,
}: {
    title: string;
    subtitle?: string;
    children?: React.ReactNode;
}) {
    return (
        <div className="docx-preview">
            <div className="docx-preview__icon">
                <DocIcon />
            </div>
            <div className="docx-preview__meta">
                <div className="docx-preview__name">{title}</div>
                {subtitle ? <div className="docx-preview__subtitle">{subtitle}</div> : null}
            </div>
            {children}
        </div>
    );
}

/** The masking result pane: result / keys tabs, stats and two downloads. */
function MaskResultPane({
    error,
    resultTab,
    onResultTabChange,
    stats,
    keys,
    maskedNothing,
    showResultPreview,
    maskedFile,
    inFileMode,
    result,
    resultTextRef,
    maskedDocRef,
    canDownloadResult,
    canDownloadKeys,
    onDownloadResult,
    onDownloadKeys,
}: {
    error: string | null;
    resultTab: string;
    onResultTabChange: (key: string) => void;
    stats: MaskStats | null;
    keys: string;
    maskedNothing: boolean;
    showResultPreview: boolean;
    maskedFile: MaskedFileResult | null;
    inFileMode: boolean;
    result: string;
    resultTextRef: RefObject<HTMLTextAreaElement | null>;
    maskedDocRef: RefObject<HTMLDivElement | null>;
    canDownloadResult: boolean;
    canDownloadKeys: boolean;
    onDownloadResult: () => void;
    onDownloadKeys: () => void;
}) {
    return (
        <>
            <div className="editor-pane__label">
                <Tabs
                    className="result-tabs"
                    selectedKey={resultTab}
                    onSelectionChange={(key) => onResultTabChange(String(key))}
                >
                    <Tabs.ListContainer>
                        <Tabs.List aria-label="Результат маскировки">
                            <Tabs.Tab id="result">
                                Результат маскировки
                                <Tabs.Indicator />
                            </Tabs.Tab>
                            <Tabs.Tab id="keys" isDisabled={!canDownloadKeys}>
                                Ключи демаскировки
                                <Tabs.Indicator />
                            </Tabs.Tab>
                        </Tabs.List>
                    </Tabs.ListContainer>
                </Tabs>
                {stats !== null && error === null && stats.total > 0 ? (
                    <Tooltip delay={150}>
                        <Tooltip.Trigger className="editor-pane__stats">
                            {totalText(stats)}
                        </Tooltip.Trigger>
                        <Tooltip.Content showArrow placement="bottom">
                            <Tooltip.Arrow />
                            <span className="stats-breakdown">{statsBreakdown(stats)}</span>
                        </Tooltip.Content>
                    </Tooltip>
                ) : null}
            </div>
            <ResultBody
                error={error}
                resultTab={resultTab}
                keys={keys}
                maskedNothing={maskedNothing}
                showResultPreview={showResultPreview}
                maskedFile={maskedFile}
                inFileMode={inFileMode}
                result={result}
                resultTextRef={resultTextRef}
                maskedDocRef={maskedDocRef}
            />
            <div className="editor-pane__actions">
                <ButtonGroup fullWidth>
                    <Button
                        variant="primary"
                        onPress={onDownloadResult}
                        fullWidth
                        {...disabledProps(!canDownloadResult)}
                    >
                        Скачать результат маскировки
                    </Button>
                    <Button
                        variant="tertiary"
                        onPress={onDownloadKeys}
                        fullWidth
                        {...disabledProps(!canDownloadKeys)}
                    >
                        Скачать ключи демаскировки
                    </Button>
                </ButtonGroup>
            </div>
        </>
    );
}

/** The editable body of the masking result pane (error / keys / result). */
function ResultBody({
    error,
    resultTab,
    keys,
    maskedNothing,
    showResultPreview,
    maskedFile,
    inFileMode,
    result,
    resultTextRef,
    maskedDocRef,
}: {
    error: string | null;
    resultTab: string;
    keys: string;
    maskedNothing: boolean;
    showResultPreview: boolean;
    maskedFile: MaskedFileResult | null;
    inFileMode: boolean;
    result: string;
    resultTextRef: RefObject<HTMLTextAreaElement | null>;
    maskedDocRef: RefObject<HTMLDivElement | null>;
}) {
    // Stable identity so the preview is not re-highlighted on unrelated renders.
    const placeholders = useMemo(() => placeholdersFromKeys(keys), [keys]);

    if (error !== null) {
        return (
            <TextArea
                aria-label="Ошибка"
                aria-invalid
                className="result-area--error"
                value={`Ошибка: ${error}`}
                readOnly
                rows={12}
            />
        );
    }
    if (resultTab === "keys") {
        return (
            <TextArea
                aria-label="Ключи демаскировки"
                className="result-area--keys"
                value={keys}
                readOnly
                rows={12}
            />
        );
    }
    if (maskedNothing) {
        return (
            <TextArea
                aria-label="Результат"
                className="result-area--empty"
                value="Маскировка не выполнена: в тексте не нашлось чувствительной информации."
                readOnly
                rows={12}
            />
        );
    }
    if (showResultPreview && maskedFile) {
        return (
            <DocxViewer
                blob={maskedFile.result}
                surfaceRef={maskedDocRef}
                highlight={placeholders}
            />
        );
    }
    if (inFileMode) {
        return (
            <DocPreviewShell
                title="Ожидание"
                subtitle="Файл замаскируется после нажатия «Замаскировать»"
            />
        );
    }
    return (
        <TextArea
            ref={resultTextRef}
            aria-label="Результат"
            placeholder="Здесь появится замаскированный текст..."
            value={result}
            readOnly
            rows={12}
        />
    );
}

/** The demasking result pane: the restored text or document, and its download. */
function UnmaskResultPane({
    error,
    restored,
    restoredFile,
    restoredDocRef,
    demaskKeys,
    inFileMode,
    canDownload,
    onDownload,
}: {
    error: string | null;
    restored: string;
    restoredFile: DocxResult | null;
    restoredDocRef: RefObject<HTMLDivElement | null>;
    demaskKeys: string;
    inFileMode: boolean;
    canDownload: boolean;
    onDownload: () => void;
}) {
    // Stable identity so editing the keys does not re-highlight the preview.
    const restoredValues = useMemo(() => valuesFromKeys(demaskKeys), [demaskKeys]);

    return (
        <>
            <div className="editor-pane__label">
                <span>Результат демаскировки</span>
            </div>
            {error !== null ? (
                <TextArea
                    aria-label="Ошибка"
                    aria-invalid
                    className="result-area--error"
                    value={`Ошибка: ${error}`}
                    readOnly
                    rows={12}
                />
            ) : restoredFile ? (
                <DocxViewer
                    blob={restoredFile.blob}
                    surfaceRef={restoredDocRef}
                    highlight={restoredValues}
                />
            ) : inFileMode ? (
                <DocPreviewShell
                    title="Ожидание"
                    subtitle="Файл демаскируется после нажатия «Демаскировать»"
                />
            ) : (
                <TextArea
                    aria-label="Результат демаскировки"
                    placeholder="Здесь появится восстановленный текст..."
                    value={restored}
                    readOnly
                    rows={12}
                />
            )}
            <div className="editor-pane__actions">
                <Button
                    variant="primary"
                    onPress={onDownload}
                    fullWidth
                    {...disabledProps(!canDownload)}
                >
                    Скачать файл
                </Button>
            </div>
        </>
    );
}

/** Drag-and-drop handlers shared by both input panes. */
interface DragHandlers {
    dragging: boolean;
    onDragEnter: (event: DragEvent<HTMLDivElement>) => void;
    onDragOver: (event: DragEvent<HTMLDivElement>) => void;
    onDragLeave: (event: DragEvent<HTMLDivElement>) => void;
    onDrop: (event: DragEvent<HTMLDivElement>) => void;
}

/** The masking input pane: text or `.docx`, and the mask action. */
function MaskInputPane({
    input,
    inputTextRef,
    sourceDocRef,
    selectedFile,
    inFileMode,
    busy,
    maskEnabled,
    drag,
    onTextChange,
    onFileActionPress,
    onMask,
}: {
    input: string;
    inputTextRef: RefObject<HTMLTextAreaElement | null>;
    sourceDocRef: RefObject<HTMLDivElement | null>;
    selectedFile: File | null;
    inFileMode: boolean;
    busy: boolean;
    maskEnabled: boolean;
    drag: DragHandlers;
    onTextChange: (value: string) => void;
    onFileActionPress: () => void;
    onMask: () => void;
}) {
    const { dragging, ...dragHandlers } = drag;
    return (
        <div className={`editor-pane${dragging ? " editor-pane--dragging" : ""}`} {...dragHandlers}>
            <div className="editor-pane__label">
                <span>
                    {inFileMode && selectedFile ? selectedFile.name : "Данные для маскировки"}
                </span>
            </div>
            {inFileMode && selectedFile ? (
                <DocxViewer blob={selectedFile} surfaceRef={sourceDocRef} />
            ) : (
                <TextArea
                    ref={inputTextRef}
                    aria-label="Данные для маскировки"
                    placeholder="Введите или вставьте текст с чувствительной информацией..."
                    value={input}
                    onChange={(event) => onTextChange(event.target.value)}
                    rows={12}
                    disabled={busy || inFileMode}
                />
            )}
            {dragging ? (
                <div className="editor-pane__dropzone" aria-hidden="true">
                    Отпустите файл .docx или .txt, чтобы загрузить его
                </div>
            ) : null}
            <div className="editor-pane__actions">
                <ButtonGroup fullWidth>
                    <Button
                        variant={inFileMode ? "danger" : "tertiary"}
                        isDisabled={busy}
                        onPress={onFileActionPress}
                        fullWidth
                    >
                        {inFileMode ? "Удалить файл" : "Выбрать файл"}
                    </Button>
                    <Button
                        variant="primary"
                        onPress={onMask}
                        fullWidth
                        aria-disabled={!maskEnabled}
                        className={!maskEnabled ? "action-disabled" : undefined}
                    >
                        {busy ? "Маскируем…" : "Замаскировать"}
                    </Button>
                </ButtonGroup>
            </div>
        </div>
    );
}

/**
 * The demasking input pane: its own tabs — the masked data and the keys —
 * and the three actions. Kept as a separate component from `MaskInputPane` so
 * the two modes never share an input element, preview or text area.
 */
function DemaskInputPane({
    input,
    inputTextRef,
    sourceDocRef,
    selectedFile,
    inFileMode,
    busy,
    unmaskEnabled,
    keysInvalid,
    demaskKeys,
    inputTab,
    demaskStats,
    drag,
    onTextChange,
    onDemaskKeysChange,
    onKeysDrop,
    onInputTabChange,
    onDataFilePress,
    onKeysFilePress,
    onUnmask,
}: {
    input: string;
    inputTextRef: RefObject<HTMLTextAreaElement | null>;
    sourceDocRef: RefObject<HTMLDivElement | null>;
    selectedFile: File | null;
    inFileMode: boolean;
    busy: boolean;
    unmaskEnabled: boolean;
    keysInvalid: boolean;
    demaskKeys: string;
    inputTab: string;
    demaskStats: MaskStats | null;
    drag: DragHandlers;
    onTextChange: (value: string) => void;
    onDemaskKeysChange: (value: string) => void;
    onKeysDrop: (event: DragEvent<HTMLDivElement>) => void;
    onInputTabChange: (key: string) => void;
    onDataFilePress: () => void;
    onKeysFilePress: () => void;
    onUnmask: () => void;
}) {
    const { dragging, ...dragHandlers } = drag;
    return (
        <div className={`editor-pane${dragging ? " editor-pane--dragging" : ""}`} {...dragHandlers}>
            <div className="editor-pane__label">
                <Tabs
                    className="result-tabs"
                    selectedKey={inputTab}
                    onSelectionChange={(key) => onInputTabChange(String(key))}
                >
                    <Tabs.ListContainer>
                        <Tabs.List aria-label="Данные для демаскировки">
                            <Tabs.Tab id="data">
                                Данные для демаскировки
                                <Tabs.Indicator />
                            </Tabs.Tab>
                            <Tabs.Tab id="keys">
                                Ключи демаскировки
                                <Tabs.Indicator />
                            </Tabs.Tab>
                        </Tabs.List>
                    </Tabs.ListContainer>
                </Tabs>
                {demaskStats !== null && demaskStats.total > 0 ? (
                    <Tooltip delay={150}>
                        <Tooltip.Trigger className="editor-pane__stats">
                            {totalText(demaskStats)}
                        </Tooltip.Trigger>
                        <Tooltip.Content showArrow placement="bottom">
                            <Tooltip.Arrow />
                            <span className="stats-breakdown">{statsBreakdown(demaskStats)}</span>
                        </Tooltip.Content>
                    </Tooltip>
                ) : null}
                {keysInvalid ? (
                    <div className="editor-pane__keys-error">Некорректный формат.</div>
                ) : null}
            </div>
            {inputTab === "keys" ? (
                <div
                    className="keys-editor"
                    onDragOver={(event) => event.preventDefault()}
                    onDrop={onKeysDrop}
                >
                    <TextArea
                        aria-label="Ключи демаскировки"
                        aria-invalid={keysInvalid}
                        className="result-area--keys"
                        placeholder="Вставьте ключи демаскировки или перетащите файл .yaml"
                        value={demaskKeys}
                        onChange={(event) => onDemaskKeysChange(event.target.value)}
                        rows={12}
                    />
                </div>
            ) : inFileMode && selectedFile ? (
                <DocxViewer blob={selectedFile} surfaceRef={sourceDocRef} />
            ) : (
                <TextArea
                    ref={inputTextRef}
                    aria-label="Данные для демаскировки"
                    placeholder="Введите или вставьте замаскированный текст..."
                    value={input}
                    onChange={(event) => onTextChange(event.target.value)}
                    rows={12}
                    disabled={busy || inFileMode}
                />
            )}
            {dragging ? (
                <div className="editor-pane__dropzone" aria-hidden="true">
                    Отпустите файл .docx или .txt, чтобы загрузить его
                </div>
            ) : null}
            <div className="editor-pane__actions">
                <ButtonGroup fullWidth>
                    <Button
                        variant={inFileMode ? "danger" : "tertiary"}
                        isDisabled={busy}
                        onPress={onDataFilePress}
                        fullWidth
                    >
                        {inFileMode ? "Удалить файл" : "Выбрать файл данных"}
                    </Button>
                    <Button
                        variant="tertiary"
                        isDisabled={busy}
                        onPress={onKeysFilePress}
                        fullWidth
                    >
                        Выбрать файл ключей
                    </Button>
                    <Button
                        variant="primary"
                        onPress={onUnmask}
                        fullWidth
                        aria-disabled={!unmaskEnabled}
                        className={!unmaskEnabled ? "action-disabled" : undefined}
                    >
                        {busy ? "Демаскируем…" : "Демаскировать"}
                    </Button>
                </ButtonGroup>
            </div>
        </div>
    );
}

export default function Home() {
    const [mode, setMode] = useState<AppMode>("mask");
    const inMaskMode = mode === "mask";

    // The tab title follows the active mode (the metadata title is static).
    useEffect(() => {
        document.title = inMaskMode ? "ДокМаскер — Маскировка" : "ДокМаскер — Демаскировка";
    }, [inMaskMode]);

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
        inputTab,
        resultTab,
        busy,
        error,
    } = state;
    const dragging = state.dragging;

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
              ...countKeysValues(demaskKeys, inFileMode ? null : input),
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

    // Switching flows keeps both drafts; nothing is cleared here.
    function onModeChange(next: AppMode) {
        setMode(next);
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
            const data = (await response.json()) as MaskResponse;
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
            update({ busy: false });
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
            const total = Number(response.headers.get("X-Masked-Count") ?? "0");
            const byLabel = parseBreakdown(response.headers.get("X-Masked-Breakdown"));
            // The backend returns one ZIP holding the masked .docx and its keys;
            // unpack it so each half can be downloaded on its own.
            const bundle = await unpackMaskArchive(await response.blob());
            const keysYaml = await bundle.keys.text();
            // Keys are deduped by value, so their per-label count is the unique
            // count (the headers only carry occurrence totals).
            const uniqueByLabel: Record<string, number> = {};
            for (const [placeholder] of keyEntries(keysYaml)) {
                const label = placeholder.slice(1, -1).replace(/_\d+$/, "").toLowerCase();
                uniqueByLabel[label] = (uniqueByLabel[label] ?? 0) + 1;
            }
            update({
                maskedFile: bundle,
                keys: keysYaml,
                stats: { total, byLabel, uniqueByLabel, unused: [] },
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
            update({ busy: false });
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
                const bundle = await unpackRestoreArchive(await response.blob());
                update({
                    restoredFile: { blob: bundle.docx, name: bundle.docxName },
                    demaskUnused: bundle.manifest.unused_keys,
                });
            } else {
                const response = await requestTextUnmask();
                if (!response.ok) {
                    throw new Error(await readErrorDetail(response));
                }
                const data = (await response.json()) as UnmaskResponse;
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
            update({ busy: false });
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

    /** Masking file button: remove the selected file, or open the picker. */
    function onMaskFileActionPress() {
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

    /** Demasking data-file button: remove the selected file, or open the picker. */
    function onDataFileActionPress() {
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

    return (
        <div className="app-shell">
            <header className="app-header">
                <Brand mode={mode} onModeChange={onModeChange} />
                <div className="app-header__end">
                    <ThemeSwitcher />
                </div>
            </header>

            <main className="masker-body">
                <div className="editor-grid">
                    {inMaskMode ? (
                        <MaskInputPane
                            input={input}
                            inputTextRef={inputTextRef}
                            sourceDocRef={sourceDocRef}
                            selectedFile={selectedFile}
                            inFileMode={inFileMode}
                            busy={busy}
                            maskEnabled={maskEnabled}
                            drag={drag}
                            onTextChange={onTextChange}
                            onFileActionPress={onMaskFileActionPress}
                            onMask={handleMask}
                        />
                    ) : (
                        <DemaskInputPane
                            input={input}
                            inputTextRef={inputTextRef}
                            sourceDocRef={sourceDocRef}
                            selectedFile={selectedFile}
                            inFileMode={inFileMode}
                            busy={busy}
                            unmaskEnabled={unmaskEnabled}
                            keysInvalid={keysInvalid}
                            demaskKeys={demaskKeys}
                            inputTab={inputTab}
                            demaskStats={demaskStats}
                            drag={drag}
                            onTextChange={onTextChange}
                            onDemaskKeysChange={(value) => update({ demaskKeys: value })}
                            onKeysDrop={onKeysDrop}
                            onInputTabChange={(key) => update({ inputTab: key })}
                            onDataFilePress={onDataFileActionPress}
                            onKeysFilePress={onKeysFileActionPress}
                            onUnmask={handleUnmask}
                        />
                    )}

                    <div className="editor-pane">
                        {inMaskMode ? (
                            <MaskResultPane
                                error={error}
                                resultTab={resultTab}
                                onResultTabChange={(key) => update({ resultTab: key })}
                                stats={stats}
                                keys={keys}
                                maskedNothing={maskedNothing}
                                showResultPreview={showResultPreview}
                                maskedFile={maskedFile}
                                inFileMode={inFileMode}
                                result={result}
                                resultTextRef={resultTextRef}
                                maskedDocRef={maskedDocRef}
                                canDownloadResult={canDownloadResult}
                                canDownloadKeys={canDownloadKeys}
                                onDownloadResult={downloadResult}
                                onDownloadKeys={downloadKeys}
                            />
                        ) : (
                            <UnmaskResultPane
                                error={error}
                                restored={restored}
                                restoredFile={restoredFile}
                                restoredDocRef={restoredDocRef}
                                demaskKeys={demaskKeys}
                                inFileMode={inFileMode}
                                canDownload={canDownloadRestored}
                                onDownload={downloadRestored}
                            />
                        )}
                    </div>
                </div>

                <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    hidden
                    onChange={onFileSelected}
                />
                <input
                    ref={keysInputRef}
                    type="file"
                    accept=".yaml,.yml"
                    hidden
                    onChange={onKeysSelected}
                />
            </main>
        </div>
    );
}
