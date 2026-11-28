import { expect, test } from "@playwright/test";

import { MASK_DATA, SAMPLE_DOCX } from "./_helpers";
import { DATA_FILE_INPUT } from "./helpers/selectors";

test("текстовая маскировка через job (202) показывает результат", async ({ page }) => {
    const jobId = "abcdefab-1234-4234-8234-abcdefabcdef";
    const statusUrl = `/api/jobs/${jobId}`;
    const resultUrl = `/api/jobs/${jobId}/result`;

    // Build a one-entry result ZIP the same way the backend does (flat archive).
    const JSZip = (await import("jszip")).default;
    const zip = new JSZip();
    zip.file(
        "result.json",
        JSON.stringify({
            text: "[PERSON_1]",
            keys: "Физлица:\n  - Иван Петров",
            stats: {
                total: 1,
                occurrences_by_label: { person: 1 },
                unique_by_label: { person: 1 },
            },
        }),
    );
    const resultZip = Buffer.from(await zip.generateAsync({ type: "nodebuffer" }));

    await page.route("**/api/mask", async (route) => {
        await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify({
                job_id: jobId,
                status: "queued",
                status_url: statusUrl,
                result_url: resultUrl,
            }),
        });
    });
    await page.route(`**${statusUrl}`, async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                job_id: jobId,
                status: "succeeded",
                phase: "completed",
                progress: 100,
                attempt: 1,
                error_code: null,
                result_url: resultUrl,
            }),
        });
    });
    await page.route(`**${resultUrl}`, async (route) => {
        await route.fulfill({ status: 200, contentType: "application/zip", body: resultZip });
    });

    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue("[PERSON_1]", {
        timeout: 15000,
    });
});

test("активное задание показывает «Отменить операцию» вместо «Выбрать файл» и шлёт DELETE", async ({
    page,
}) => {
    const jobId = "abcdefab-1234-4234-8234-abcdefabcdef";
    let cancelled = false;
    await page.route("**/api/mask", async (route) => {
        await route.fulfill({
            status: 202,
            contentType: "application/json",
            body: JSON.stringify({
                job_id: jobId,
                status: "queued",
                status_url: `/api/jobs/${jobId}`,
                result_url: `/api/jobs/${jobId}/result`,
            }),
        });
    });
    // Keep the job pending so the Cancel control stays visible.
    await page.route(`**/api/jobs/${jobId}`, async (route) => {
        if (route.request().method() === "DELETE") {
            cancelled = true;
            await route.fulfill({ status: 204, body: "" });
            return;
        }
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                job_id: jobId,
                status: "running",
                phase: "detection",
                progress: 30,
                attempt: 1,
                error_code: null,
                result_url: null,
            }),
        });
    });

    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    const cancel = page.getByRole("button", { name: "Отменить операцию" });
    await expect(cancel).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("button", { name: "Выбрать файл" })).toHaveCount(0);
    await cancel.click();
    await expect(cancel).toHaveCount(0);
    expect(cancelled).toBe(true);
});

test("результат маскировки файла требует manifest.json и берёт статистику из него", async ({
    page,
}) => {
    const JSZip = (await import("jszip")).default;
    const manifest = {
        schema_version: 1,
        operation: "mask_file",
        status: "succeeded",
        complete: true,
        masking_ruleset_version: 1,
        exception_config_digest: "digest",
        input: { sha256: "abc", size_bytes: 12 },
        output: {
            document_name: "doc (замаскированный).docx",
            keys_name: "doc (ключи демаскировки).yaml",
        },
        statistics: {
            total_occurrences: 2,
            occurrences_by_label: { person: 1, email: 1 },
            unique_by_label: { person: 1, email: 1 },
        },
        coverage: {
            processed_parts: ["main_document", "footnotes"],
            unprocessed_text_parts: [],
            rejected_parts: [],
            raster_image_ocr: "not_performed",
        },
        cache: { hit: false, namespace_version: 1 },
    };
    const zip = new JSZip();
    zip.file("doc (замаскированный).docx", "masked-docx");
    zip.file("doc (ключи демаскировки).yaml", "Физлица:\n  - Иван Петров");
    zip.file("manifest.json", JSON.stringify(manifest));
    const archive = Buffer.from(await zip.generateAsync({ type: "nodebuffer" }));

    await page.route("**/api/mask-file", async (route) => {
        await route.fulfill({
            status: 200,
            contentType: "application/zip",
            headers: { "Content-Disposition": 'attachment; filename="doc (замаскированный).zip"' },
            body: archive,
        });
    });

    await page.goto("/");
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    // Statistics come from the manifest, not from headers.
    await expect(page.locator(".editor-pane__stats")).toHaveText("2 фрагмента", { timeout: 15000 });
    await expect(page.getByRole("button", { name: "Скачать ключи демаскировки" })).toBeEnabled();

    // A missing/malformed manifest is a clear result error, never "0 entities".
    const badZip = new JSZip();
    badZip.file("doc (замаскированный).docx", "masked-docx");
    badZip.file("doc (ключи демаскировки).yaml", "Физлица:\n  - Иван Петров");
    const badArchive = Buffer.from(await badZip.generateAsync({ type: "nodebuffer" }));
    await page.unroute("**/api/mask-file");
    await page.route("**/api/mask-file", async (route) => {
        await route.fulfill({ status: 200, contentType: "application/zip", body: badArchive });
    });
    await page.getByRole("button", { name: "Удалить файл" }).click();
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByText(/отчёт о результате/i)).toBeVisible({ timeout: 15000 });
});
