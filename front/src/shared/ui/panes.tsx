"use client";

import { Button, ButtonGroup, Tabs, TextArea, Tooltip } from "@heroui/react";
import { type DragEvent, type RefObject, useMemo } from "react";

import { type DocxResult, type MaskedFileResult } from "@/features/masking/types";
import { placeholdersFromKeys, valuesFromKeys } from "@/shared/api/mask";
import { type MaskStats, statsBreakdown, totalText } from "@/shared/mask-stats";
import type { KeysParseResult } from "@/shared/masking/keys-parse";
import { DocxViewer } from "@/shared/ui/docx-viewer";

export function DocIcon() {
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
export function disabledProps(disabled: boolean) {
    return {
        "aria-disabled": disabled,
        className: disabled ? "action-disabled" : undefined,
    } as const;
}

export function DocPreviewShell({
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
export function MaskResultPane({
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
export function ResultBody({
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
export function UnmaskResultPane({
    error,
    restored,
    restoredFile,
    restoredDocRef,
    parsedDemaskKeys,
    inFileMode,
    canDownload,
    onDownload,
}: {
    error: string | null;
    restored: string;
    restoredFile: DocxResult | null;
    restoredDocRef: RefObject<HTMLDivElement | null>;
    parsedDemaskKeys: KeysParseResult;
    inFileMode: boolean;
    canDownload: boolean;
    onDownload: () => void;
}) {
    // Stable identity so editing the keys does not re-highlight the preview.
    // The workflow already parsed this string once; reuse that result.
    const restoredValues = useMemo(() => valuesFromKeys(parsedDemaskKeys), [parsedDemaskKeys]);

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
export interface DragHandlers {
    dragging: boolean;
    onDragEnter: (event: DragEvent<HTMLDivElement>) => void;
    onDragOver: (event: DragEvent<HTMLDivElement>) => void;
    onDragLeave: (event: DragEvent<HTMLDivElement>) => void;
    onDrop: (event: DragEvent<HTMLDivElement>) => void;
}

/** The masking input pane: text or `.docx`, and the mask action. */
export function MaskInputPane({
    input,
    inputTextRef,
    sourceDocRef,
    selectedFile,
    inFileMode,
    busy,
    maskEnabled,
    activeJob,
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
    activeJob: boolean;
    drag: DragHandlers;
    onTextChange: (value: string) => void;
    onFileActionPress: () => void;
    onMask: () => void;
}) {
    const { dragging, ...dragHandlers } = drag;
    return (
        <div className={`editor-pane${dragging ? " editor-pane--dragging" : ""}`} {...dragHandlers}>
            <div className="editor-pane__label editor-pane__label--mask-input">
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
                        variant={activeJob ? "danger" : inFileMode ? "danger" : "tertiary"}
                        isDisabled={busy && !activeJob}
                        onPress={onFileActionPress}
                        fullWidth
                    >
                        {activeJob
                            ? "Отменить задание"
                            : inFileMode
                              ? "Удалить файл"
                              : "Выбрать файл"}
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
export function DemaskInputPane({
    input,
    inputTextRef,
    sourceDocRef,
    selectedFile,
    inFileMode,
    busy,
    unmaskEnabled,
    activeJob,
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
    activeJob: boolean;
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
                        variant={activeJob ? "danger" : inFileMode ? "danger" : "tertiary"}
                        isDisabled={busy && !activeJob}
                        onPress={onDataFilePress}
                        fullWidth
                    >
                        {activeJob
                            ? "Отменить задание"
                            : inFileMode
                              ? "Удалить файл"
                              : "Выбрать файл данных"}
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
