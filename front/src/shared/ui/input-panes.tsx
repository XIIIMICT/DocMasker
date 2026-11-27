"use client";

import { Button, ButtonGroup, Tabs, TextArea, Tooltip } from "@heroui/react";
import { type DragEvent, type RefObject } from "react";

import { type MaskStats, statsBreakdown, totalText } from "@/shared/mask-stats";
import { DocxViewer } from "@/shared/ui/docx-viewer";
import type { DragHandlers } from "@/shared/ui/pane-types";

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
                            ? "Отменить операцию"
                            : inFileMode
                              ? "Удалить файл"
                              : "Выбрать файл"}
                    </Button>
                    <Button
                        variant="primary"
                        onPress={onMask}
                        fullWidth
                        aria-disabled={!maskEnabled}
                        {...(!maskEnabled ? { className: "action-disabled" } : {})}
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
                    <div className="editor-pane__keys-error" id="demask-keys-error">
                        Некорректный формат.
                    </div>
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
                        aria-describedby={keysInvalid ? "demask-keys-error" : undefined}
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
                            ? "Отменить операцию"
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
                        {...(!unmaskEnabled ? { className: "action-disabled" } : {})}
                    >
                        {busy ? "Демаскируем…" : "Демаскировать"}
                    </Button>
                </ButtonGroup>
            </div>
        </div>
    );
}
