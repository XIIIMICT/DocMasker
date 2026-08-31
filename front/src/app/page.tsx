"use client";

import { Button, TextArea } from "@heroui/react";
import { useRef, useState } from "react";

import type { MaskResponse } from "@/shared/api/mask";
import { DocxViewer } from "@/shared/ui/docx-viewer";

interface MaskedFileResult {
    blob: Blob;
    downloadName: string;
    maskedCount: number;
}

function parseDispositionFilename(disposition: string | null): string {
    if (!disposition) return "";
    // Prefer the RFC 5987 `filename*=UTF-8''<percent-encoded>` value (keeps Cyrillic).
    const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (star?.[1]) {
        try {
            return decodeURIComponent(star[1]);
        } catch {
            /* fall through to the ASCII fallback */
        }
    }
    return disposition.match(/filename="([^"]+)"/)?.[1] ?? "";
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

export default function Home() {
    const [input, setInput] = useState("");
    const [result, setResult] = useState("");
    const [entityCount, setEntityCount] = useState<number | null>(null);
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    const [maskedFile, setMaskedFile] = useState<MaskedFileResult | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const hasText = input.trim().length > 0;
    const hasFile = selectedFile !== null;
    // Exactly one input mode is active; a selected file takes precedence.
    const inFileMode = hasFile;
    const maskEnabled = !busy && (hasText || hasFile);

    function onTextChange(value: string) {
        if (hasFile) {
            setSelectedFile(null);
            setMaskedFile(null);
        }
        setInput(value);
        setEntityCount(null);
        setError(null);
    }

    function onFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;
        setInput("");
        setResult("");
        setEntityCount(null);
        setMaskedFile(null);
        setSelectedFile(file);
        setError(null);
    }

    async function handleMask() {
        if (busy) return;
        setError(null);
        if (!hasText && !hasFile) return;

        if (hasFile) {
            await handleFileMask();
        } else {
            await handleTextMask();
        }
    }

    async function handleTextMask() {
        setBusy(true);
        try {
            const response = await fetch("/api/mask", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: input }),
            });
            if (!response.ok) {
                const payload = (await response.json().catch(() => null)) as {
                    detail?: string;
                } | null;
                throw new Error(payload?.detail ?? `Ошибка сервера (${response.status}).`);
            }
            const data = (await response.json()) as MaskResponse;
            setResult(data.text);
            setEntityCount(data.masked_entities.length);
        } catch (requestError) {
            setResult("");
            setEntityCount(null);
            setError(
                requestError instanceof Error
                    ? requestError.message
                    : "Не удалось выполнить маскирование.",
            );
        } finally {
            setBusy(false);
        }
    }

    async function handleFileMask() {
        const file = fileInputRef.current?.files?.[0];
        if (!file) return;
        setBusy(true);
        try {
            const formData = new FormData();
            formData.append("file", file, file.name);
            const response = await fetch("/api/mask-file", {
                method: "POST",
                body: formData,
            });
            if (!response.ok) {
                const payload = (await response.json().catch(() => null)) as {
                    detail?: string;
                } | null;
                throw new Error(payload?.detail ?? `Ошибка сервера (${response.status}).`);
            }
            const maskedCount = Number(response.headers.get("X-Masked-Count") ?? "0");
            const downloadName = parseDispositionFilename(
                response.headers.get("Content-Disposition"),
            );
            const blob = await response.blob();
            setMaskedFile({ blob, downloadName, maskedCount });
            setEntityCount(maskedCount);
        } catch (requestError) {
            setMaskedFile(null);
            setEntityCount(null);
            setError(
                requestError instanceof Error
                    ? requestError.message
                    : "Не удалось обработать файл.",
            );
        } finally {
            setBusy(false);
        }
    }

    function downloadMaskedFile() {
        if (!maskedFile) return;
        const url = URL.createObjectURL(maskedFile.blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = maskedFile.downloadName;
        anchor.click();
        URL.revokeObjectURL(url);
    }

    function chooseFile() {
        if (!busy) fileInputRef.current?.click();
    }

    return (
        <div className="app-shell">
            <header className="app-header">
                <div className="brand" aria-label="ДокМаскер">
                    <img
                        className="brand__icon"
                        src="/ruschem_icon.png"
                        alt=""
                        onError={(event) => {
                            event.currentTarget.hidden = true;
                        }}
                    />
                    <span className="brand__name">ДокМаскер</span>
                </div>
            </header>

            <main className="masker-body">
                <div className="editor-grid">
                    <div className="editor-pane">
                        {inFileMode && selectedFile ? (
                            <>
                                <div className="docx-preview__label">{selectedFile.name}</div>
                                <DocxViewer blob={selectedFile} />
                            </>
                        ) : (
                            <TextArea
                                aria-label="Входной текст"
                                placeholder="Введите или вставьте текст с чувствительной информацией..."
                                value={input}
                                onChange={(event) => onTextChange(event.target.value)}
                                rows={12}
                                disabled={busy || hasFile}
                            />
                        )}
                        <div className="editor-pane__actions">
                            <div className="action-group">
                                <Button
                                    variant="tertiary"
                                    isDisabled={busy}
                                    onPress={chooseFile}
                                    fullWidth
                                >
                                    {inFileMode ? selectedFile?.name : "Выбрать файл"}
                                </Button>
                                <button
                                    type="button"
                                    className="action-group__mask"
                                    onClick={handleMask}
                                    disabled={!maskEnabled}
                                >
                                    {busy ? "Маскируем…" : "Замаскировать"}
                                </button>
                            </div>
                            {error ? (
                                <span className="masker-actions__error">Ошибка: {error}</span>
                            ) : null}
                            {!error && entityCount !== null && !inFileMode ? (
                                <span className="masker-actions__status">
                                    Готово — замаскировано фрагментов: {entityCount}.
                                </span>
                            ) : null}
                        </div>
                    </div>

                    <div className="editor-pane">
                        {inFileMode ? (
                            maskedFile ? (
                                <>
                                    <div className="docx-preview__label">
                                        {maskedFile.downloadName}
                                        <span className="docx-preview__count">
                                            Замаскировано фрагментов: {maskedFile.maskedCount}
                                        </span>
                                    </div>
                                    <DocxViewer blob={maskedFile.blob} />
                                    <Button variant="primary" onPress={downloadMaskedFile}>
                                        Скачать файл
                                    </Button>
                                </>
                            ) : (
                                <DocPreviewShell
                                    title="Ожидание"
                                    subtitle="Файл замаскируется после нажатия «Замаскировать»"
                                />
                            )
                        ) : (
                            <TextArea
                                aria-label="Результат"
                                placeholder="Здесь появится замаскированный текст..."
                                value={result}
                                readOnly
                                rows={12}
                            />
                        )}
                    </div>
                </div>

                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".docx"
                    hidden
                    onChange={onFileSelected}
                />
            </main>
        </div>
    );
}
