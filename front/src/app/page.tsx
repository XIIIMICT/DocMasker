"use client";

import { Button, TextArea } from "@heroui/react";
import { useRef, useState } from "react";

import type { MaskResponse } from "@/shared/api/mask";

export default function Home() {
    const [input, setInput] = useState("");
    const [result, setResult] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(false);
    const [entityCount, setEntityCount] = useState<number | null>(null);
    const [fileLoading, setFileLoading] = useState(false);
    const [fileResult, setFileResult] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    async function handleMask() {
        if (!input.trim()) {
            setError("Введите текст для маскирования.");
            return;
        }
        setLoading(true);
        setError(null);
        setEntityCount(null);
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
            setError(
                requestError instanceof Error
                    ? requestError.message
                    : "Не удалось выполнить маскирование.",
            );
        } finally {
            setLoading(false);
        }
    }

    async function handleMaskFile(file: File) {
        setFileLoading(true);
        setError(null);
        setFileResult(null);
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

            const maskedCount = response.headers.get("X-Masked-Count") ?? "0";
            const disposition = response.headers.get("Content-Disposition");
            const downloadName =
                disposition?.match(/filename="([^"]+)"/)?.[1] ?? `${file.name}_masked.docx`;

            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = downloadName;
            anchor.click();
            URL.revokeObjectURL(url);

            setFileResult(
                `Замаскировано фрагментов: ${maskedCount}. Файл сохранён: ${downloadName}.`,
            );
        } catch (requestError) {
            setFileResult(null);
            setError(
                requestError instanceof Error
                    ? requestError.message
                    : "Не удалось обработать файл.",
            );
        } finally {
            setFileLoading(false);
            if (fileInputRef.current) {
                fileInputRef.current.value = "";
            }
        }
    }

    function onFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
        const file = event.target.files?.[0];
        if (file) {
            void handleMaskFile(file);
        }
    }

    return (
        <div className="app-shell">
            <header className="app-header">
                <div className="brand brand__name">DocMasker</div>
            </header>

            <main className="masker-body">
                <div className="editor-grid">
                    <div className="editor-pane">
                        <TextArea
                            aria-label="Входной текст"
                            placeholder="Введите или вставьте текст с чувствительной информацией..."
                            value={input}
                            onChange={(event) => setInput(event.target.value)}
                            rows={12}
                            disabled={loading}
                        />
                    </div>

                    <div className="editor-pane">
                        <TextArea
                            aria-label="Результат"
                            placeholder="Здесь появится замаскированный текст..."
                            value={result}
                            readOnly
                            rows={12}
                        />
                    </div>
                </div>

                <div className="masker-actions">
                    <Button
                        variant="primary"
                        onPress={handleMask}
                        isDisabled={loading || !input.trim()}
                    >
                        {loading ? "Маскируем…" : "Маскировать"}
                    </Button>
                    {error ? <span className="masker-actions__error">Ошибка: {error}</span> : null}
                    {!error && entityCount !== null ? (
                        <span className="masker-actions__status">
                            Готово — замаскировано фрагментов: {entityCount}.
                        </span>
                    ) : null}
                </div>

                <div className="mask-file-section">
                    <span className="mask-file-section__hint">DOCX-файл</span>
                    <Button
                        variant="tertiary"
                        isDisabled={fileLoading}
                        onPress={() => fileInputRef.current?.click()}
                    >
                        {fileLoading ? "Маскируем файл…" : "Выбрать файл и замаскировать"}
                    </Button>
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept=".docx"
                        hidden
                        onChange={onFileSelected}
                    />
                    {fileResult ? (
                        <span className="masker-actions__status">{fileResult}</span>
                    ) : null}
                </div>
            </main>
        </div>
    );
}
