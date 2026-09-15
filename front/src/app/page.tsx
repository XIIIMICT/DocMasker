"use client";

import { Button, ButtonGroup, TextArea } from "@heroui/react";
import { useRef, useState, type DragEvent } from "react";

import type { MaskResponse } from "@/shared/api/mask";
import { useSyncedScroll } from "@/shared/hooks/use-synced-scroll";
import { DocxViewer } from "@/shared/ui/docx-viewer";

interface MaskedFileResult {
    blob: Blob;
    downloadName: string;
}

/** Masking totals: `total` plus a per-label breakdown (e.g. `person: 3`). */
interface MaskStats {
    total: number;
    byLabel: Record<string, number>;
}

/** Russian plural/name forms for the entity labels the backend can return. */
const LABEL_LABELS: Record<string, [one: string, few: string, many: string]> = {
    person: ["физлицо", "физлица", "физлиц"],
    phone: ["телефон", "телефона", "телефонов"],
    email: ["эл. почта", "эл. почты", "эл. почт"],
    address: ["адрес", "адреса", "адресов"],
    organization: ["организация", "организации", "организаций"],
};

const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const TEXT_MIME = "text/plain";
const ACCEPTED_EXTENSIONS = [".docx", ".txt"];

type FileKind = "docx" | "txt";

function fileKind(file: File): FileKind | null {
    const name = file.name.toLowerCase();
    if (name.endsWith(".docx") || file.type === DOCX_MIME) return "docx";
    if (name.endsWith(".txt") || file.type === TEXT_MIME) return "txt";
    return null;
}

/** Pick the Russian plural form for `count` (1 фрагмент / 2 фрагмента / 5 фрагментов). */
function plural(count: number, forms: [string, string, string]): string {
    const mod100 = count % 100;
    const mod10 = count % 10;
    if (mod100 >= 11 && mod100 <= 14) return forms[2];
    if (mod10 === 1) return forms[0];
    if (mod10 >= 2 && mod10 <= 4) return forms[1];
    return forms[2];
}

function labelName(label: string, count: number): string {
    const forms = LABEL_LABELS[label];
    if (forms) return plural(count, forms);
    return plural(count, ["фрагмент", "фрагмента", "фрагментов"]);
}

/**
 * Two-line summary, e.g.
 * ```
 * 1 организация + 2 телефона + 4 физлица
 * = 7 фрагментов всего
 * ```
 */
function statsText(stats: MaskStats): string {
    const parts = Object.entries(stats.byLabel)
        .filter(([, count]) => count > 0)
        .sort(([, a], [, b]) => b - a)
        .map(([label, count]) => `${count} ${labelName(label, count)}`);
    const total = `${stats.total} ${plural(stats.total, ["фрагмент", "фрагмента", "фрагментов"])} всего`;
    return parts.length > 0 ? `${parts.join(" + ")}\n= ${total}` : `= ${total}`;
}

function countLabels(entities: { label: string }[]): MaskStats {
    const byLabel: Record<string, number> = {};
    for (const entity of entities) {
        byLabel[entity.label] = (byLabel[entity.label] ?? 0) + 1;
    }
    return { total: entities.length, byLabel };
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
    const [stats, setStats] = useState<MaskStats | null>(null);
    const [selectedFile, setSelectedFile] = useState<File | null>(null);
    // Base name of an uploaded `.txt`, reused for the masked download; it is
    // never shown (a `.txt` is treated as the input text, not as a document).
    const [textBaseName, setTextBaseName] = useState<string | null>(null);
    const [maskedFile, setMaskedFile] = useState<MaskedFileResult | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [dragging, setDragging] = useState(false);
    const fileInputRef = useRef<HTMLInputElement>(null);
    // Drag events fire for every child element; count enter/leave to know when
    // the pointer really left the drop zone.
    const dragDepth = useRef(0);
    // Paired scroll panes: original on the left, masked on the right. Both text
    // areas and both DOCX previews scroll in lock-step.
    const inputTextRef = useRef<HTMLTextAreaElement>(null);
    const resultTextRef = useRef<HTMLTextAreaElement>(null);
    const sourceDocRef = useRef<HTMLDivElement>(null);
    const maskedDocRef = useRef<HTMLDivElement>(null);

    const hasText = input.trim().length > 0;
    const hasFile = selectedFile !== null;
    // Exactly one input mode is active; a selected DOCX takes precedence.
    const inFileMode = hasFile;
    const maskEnabled = !busy && (hasText || hasFile);
    // An error always surfaces in the result pane, so the result is a TextArea
    // (never a document preview) while one is shown.
    const showResultPreview = inFileMode && maskedFile !== null && error === null;
    // Text mode always offers a download: the masked text is a `.txt`, whether
    // it was typed by hand or loaded from an uploaded `.txt`.
    const canDownloadText = !inFileMode && result.length > 0 && error === null;

    useSyncedScroll(inputTextRef, resultTextRef, !inFileMode);
    useSyncedScroll(sourceDocRef, maskedDocRef, showResultPreview);

    function resetResult() {
        setResult("");
        setStats(null);
        setMaskedFile(null);
        setError(null);
    }

    function onTextChange(value: string) {
        if (hasFile) {
            setSelectedFile(null);
        }
        resetResult();
        setInput(value);
    }

    function onFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (!file) return;
        void applyFile(file);
    }

    async function applyFile(file: File) {
        const kind = fileKind(file);
        if (kind === null) {
            setError("Поддерживаются файлы .docx и .txt.");
            return;
        }
        resetResult();
        if (kind === "docx") {
            setInput("");
            setTextBaseName(null);
            setSelectedFile(file);
            return;
        }
        // `.txt`: read its contents into the editable input TextArea (it is not
        // shown as a file — it is just the input text).
        try {
            const text = await file.text();
            setSelectedFile(null);
            setTextBaseName(file.name.replace(/\.txt$/i, ""));
            setInput(text);
        } catch {
            setError("Не удалось прочитать текстовый файл.");
        }
    }

    function onDragEnter(event: DragEvent<HTMLDivElement>) {
        if (busy || !event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
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
        if (dragDepth.current === 0) setDragging(false);
    }

    function onDrop(event: DragEvent<HTMLDivElement>) {
        event.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        if (busy) return;
        const file = event.dataTransfer.files?.[0];
        if (!file) return;
        void applyFile(file);
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
            setStats(countLabels(data.masked_entities));
        } catch (requestError) {
            setResult("");
            setStats(null);
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
        // Use the selected file from state so a dropped file (which never lands
        // in the hidden input) masks just like a picked one.
        const file = selectedFile;
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
            const total = Number(response.headers.get("X-Masked-Count") ?? "0");
            const byLabel = parseBreakdown(response.headers.get("X-Masked-Breakdown"));
            const downloadName = parseDispositionFilename(
                response.headers.get("Content-Disposition"),
            );
            const blob = await response.blob();
            setMaskedFile({ blob, downloadName });
            setStats({ total, byLabel });
        } catch (requestError) {
            setMaskedFile(null);
            setStats(null);
            setError(
                requestError instanceof Error
                    ? requestError.message
                    : "Не удалось обработать файл.",
            );
        } finally {
            setBusy(false);
        }
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
        downloadBlob(maskedFile.blob, maskedFile.downloadName);
    }

    function downloadMaskedText() {
        if (result.length === 0) return;
        const base = textBaseName ?? "текст";
        downloadBlob(
            new Blob([result], { type: "text/plain;charset=utf-8" }),
            `${base} (замаскированный).txt`,
        );
    }

    function chooseFile() {
        if (!busy) fileInputRef.current?.click();
    }

    function removeFile() {
        if (busy) return;
        setSelectedFile(null);
        setTextBaseName(null);
        setMaskedFile(null);
        setResult("");
        setStats(null);
        setError(null);
        if (fileInputRef.current) {
            fileInputRef.current.value = "";
        }
    }

    function onFileActionPress() {
        if (busy) return;
        if (inFileMode) {
            removeFile();
        } else {
            chooseFile();
        }
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
                    <div
                        className={`editor-pane${dragging ? " editor-pane--dragging" : ""}`}
                        onDragEnter={onDragEnter}
                        onDragOver={onDragOver}
                        onDragLeave={onDragLeave}
                        onDrop={onDrop}
                    >
                        <div className="editor-pane__label">
                            {inFileMode && selectedFile ? selectedFile.name : "Вводимый текст"}
                        </div>
                        {inFileMode && selectedFile ? (
                            <DocxViewer blob={selectedFile} surfaceRef={sourceDocRef} />
                        ) : (
                            <TextArea
                                ref={inputTextRef}
                                aria-label="Вводимый текст"
                                placeholder="Введите или вставьте текст с чувствительной информацией..."
                                value={input}
                                onChange={(event) => onTextChange(event.target.value)}
                                rows={12}
                                disabled={busy || hasFile}
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
                                    onPress={handleMask}
                                    fullWidth
                                    aria-disabled={!maskEnabled}
                                    className={!maskEnabled ? "action-disabled" : undefined}
                                >
                                    {busy ? "Маскируем…" : "Замаскировать"}
                                </Button>
                            </ButtonGroup>
                        </div>
                    </div>

                    <div className="editor-pane">
                        <div className="editor-pane__label">
                            {showResultPreview && maskedFile
                                ? maskedFile.downloadName
                                : "Замаскированный текст"}
                            {stats !== null && error === null ? (
                                <span className="editor-pane__stats">{statsText(stats)}</span>
                            ) : null}
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
                        ) : showResultPreview && maskedFile ? (
                            <>
                                <DocxViewer blob={maskedFile.blob} surfaceRef={maskedDocRef} />
                                <Button variant="primary" onPress={downloadMaskedFile} fullWidth>
                                    Скачать файл
                                </Button>
                            </>
                        ) : inFileMode ? (
                            <DocPreviewShell
                                title="Ожидание"
                                subtitle="Файл замаскируется после нажатия «Замаскировать»"
                            />
                        ) : (
                            <>
                                <TextArea
                                    ref={resultTextRef}
                                    aria-label="Результат"
                                    placeholder="Здесь появится замаскированный текст..."
                                    value={result}
                                    readOnly
                                    rows={12}
                                />
                                {canDownloadText ? (
                                    <Button
                                        variant="primary"
                                        onPress={downloadMaskedText}
                                        fullWidth
                                    >
                                        Скачать файл
                                    </Button>
                                ) : null}
                            </>
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
            </main>
        </div>
    );
}
