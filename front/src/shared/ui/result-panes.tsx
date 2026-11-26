"use client";

import { Button, ButtonGroup, Tabs, TextArea, Tooltip } from "@heroui/react";
import { type RefObject, useMemo } from "react";

import { type DocxResult, type MaskedFileResult } from "@/features/masking/types";
import { placeholdersFromKeys, valuesFromKeys } from "@/shared/api/mask";
import { type MaskStats, statsBreakdown, totalText } from "@/shared/mask-stats";
import type { KeysParseResult } from "@/shared/masking/keys-parse";
import { DocPreviewShell } from "@/shared/ui/doc-preview-shell";
import { DocxViewer } from "@/shared/ui/docx-viewer";
import { disabledProps } from "@/shared/ui/pane-types";

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
