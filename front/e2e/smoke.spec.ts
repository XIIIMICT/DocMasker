import { fileURLToPath } from "node:url";

import { expect, test, type Page } from "@playwright/test";

const MASK_DATA = "Данные для маскировки";
const DEMASK_DATA = "Данные для демаскировки";
/** A minimal .docx fixture (one paragraph with a name and a phone). */
const SAMPLE_DOCX = fileURLToPath(new URL("./fixtures/sample.docx", import.meta.url));
/** The same fixture already masked: `Контакты: [PERSON_1], тел [PHONE_1].` */
const SAMPLE_MASKED_DOCX = fileURLToPath(new URL("./fixtures/sample-masked.docx", import.meta.url));

/** Switch the app mode through the brand dropdown (opens on hover). */
async function switchMode(page: Page, name: string): Promise<void> {
    await page.getByRole("button", { name: /Режим:/ }).hover();
    await page.getByRole("menuitemradio", { name, exact: true }).click();
}

test("страница маскирования загружается с обоими полями", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("ДокМаскер")).toBeVisible();
    await expect(page.getByRole("textbox", { name: MASK_DATA })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Результат маскировки" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Замаскировать" })).toBeVisible();
});

test("заголовок вкладки следует за режимом", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle("ДокМаскер / Маскировка");
    await switchMode(page, "Демаскировка");
    await expect(page).toHaveTitle("ДокМаскер / Демаскировка");
    await switchMode(page, "Маскировка");
    await expect(page).toHaveTitle("ДокМаскер / Маскировка");
});

test("последний режим восстанавливается после перезагрузки", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await expect(page).toHaveTitle("ДокМаскер / Демаскировка");
    await page.reload();
    // The mode is restored after hydration; its controls belong to demasking.
    await expect(page).toHaveTitle("ДокМаскер / Демаскировка");
    await expect(page.getByRole("button", { name: "Демаскировать" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Ключи демаскировки" })).toBeVisible();
});

test("на мобильной ширине панели не накладываются друг на друга", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    // Regression: with 1fr rows the first pane's buttons spilled below its box
    // and landed on the second pane's tab row / «Результат …» label.
    const geometry = await page.evaluate(() => {
        const actions = [...document.querySelectorAll(".editor-pane__actions")].map((x) =>
            x.getBoundingClientRect(),
        );
        const labels = [...document.querySelectorAll(".editor-pane__label")].map((x) =>
            x.getBoundingClientRect(),
        );
        const panes = [...document.querySelectorAll(".editor-pane")].map((x) =>
            x.getBoundingClientRect(),
        );
        return {
            buttonsInsideFirstPane: actions[0].bottom <= panes[0].bottom + 1,
            buttonsBeforeNextLabel: actions[0].bottom <= labels[1].top + 1,
        };
    });
    expect(geometry.buttonsInsideFirstPane).toBe(true);
    expect(geometry.buttonsBeforeNextLabel).toBe(true);
});

test("на мобильной ширине у подписи ввода маскировки нет фиксированной высоты", async ({
    page,
}) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    // The 2.5rem min-height only centres the input label against the result
    // Tabs on desktop; on mobile the Tabs move below, so it is pure dead space.
    const minHeight = await page
        .locator(".editor-pane__label--mask-input")
        .evaluate((x) => getComputedStyle(x).minHeight);
    expect(minHeight).toBe("0px");
});

test("вкладки и кнопки видны сразу, ключи недоступны без маскировки", async ({ page }) => {
    await page.goto("/");
    const keysTab = page.getByRole("tab", { name: "Ключи демаскировки" });
    await expect(keysTab).toBeVisible();
    await expect(keysTab).toBeDisabled();
    await expect(page.getByRole("button", { name: "Скачать результат маскировки" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Скачать ключи демаскировки" })).toBeDisabled();
});

test("текст с PII маскируется через /api/mask", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров, тел +7 900 123-45-67");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    // The masked result lands in the result tab, with numbered placeholders.
    await expect
        .poll(async () => page.getByRole("textbox", { name: "Результат" }).inputValue(), {
            timeout: 30000,
        })
        .toContain("[PERSON_1]");
    const stats = page.locator(".editor-pane__stats");
    await expect(stats).toHaveText("2 фрагмента");
    await stats.focus();
    await expect(page.getByRole("tooltip")).toContainText("Физлица: 1 (1 с повторами)");
    await expect(page.getByRole("tooltip")).toContainText("Номера телефонов: 1 (1 с повторами)");
    // The keys tab carries the demasking YAML; both downloads become available.
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await expect(page.getByRole("textbox", { name: "Ключи демаскировки" })).toHaveValue(
        /Физлица:\s*\n\s+- Иван Петров/,
    );

    // Links and addresses are masked too; a common phrase is not a person.
    await page
        .getByRole("textbox", { name: MASK_DATA })
        .fill(
            "Адрес: г. Москва, ул. Ленина, д. 5. Сайт: https://example.com/path?q=1. " +
                "Это у каждой из Сторон.",
        );
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect
        .poll(async () => page.getByRole("textbox", { name: "Результат" }).inputValue(), {
            timeout: 30000,
        })
        .toContain("[LINK_1]");
    const masked = await page.getByRole("textbox", { name: "Результат" }).inputValue();
    expect(masked).toContain("[ADDRESS_1]");
    expect(masked).toContain("у каждой из Сторон");
    expect(masked).not.toContain("[PERSON");
    await expect(page.getByRole("button", { name: "Скачать результат маскировки" })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Скачать ключи демаскировки" })).toBeEnabled();
});

test("нет PII — сообщение и неактивные кнопки", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Просто текст без данных.");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(
        "Маскировка не выполнена: в тексте не нашлось чувствительной информации.",
    );
    await expect(page.getByRole("tab", { name: "Ключи демаскировки" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Скачать результат маскировки" })).toBeDisabled();
});

test("демаскировка восстанавливает текст из ключей", async ({ page }) => {
    await page.goto("/");
    // First mask a text and read its keys off the result pane.
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров, тел +7 900 123-45-67");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect
        .poll(async () => page.getByRole("textbox", { name: "Результат" }).inputValue(), {
            timeout: 30000,
        })
        .toContain("[PERSON_1]");
    const masked = await page.getByRole("textbox", { name: "Результат" }).inputValue();
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    const keys = await page.getByRole("textbox", { name: "Ключи демаскировки" }).inputValue();

    // Switch to demasking: fill the data tab, then the keys, then demask.
    await switchMode(page, "Демаскировка");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill(masked);
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page.getByRole("textbox", { name: "Ключи демаскировки" }).fill(keys);
    await page.getByRole("button", { name: "Демаскировать" }).click();
    await expect(page.getByRole("textbox", { name: "Результат демаскировки" })).toHaveValue(
        "Иван Петров, тел +7 900 123-45-67",
    );
    await expect(page.getByRole("button", { name: "Скачать файл" })).toBeEnabled();
});

test("состояние каждого режима сохраняется при переключении", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("черновик маскировки");
    await switchMode(page, "Демаскировка");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill("черновик демаскировки");
    // Back to masking: the mask draft is still there, and the demask draft is
    // not leaked into it.
    await switchMode(page, "Маскировка");
    await expect(page.getByRole("textbox", { name: MASK_DATA })).toHaveValue("черновик маскировки");
    await switchMode(page, "Демаскировка");
    await expect(page.getByRole("textbox", { name: DEMASK_DATA })).toHaveValue(
        "черновик демаскировки",
    );
});

test("на демаскировке три кнопки видны сразу", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await expect(page.getByRole("button", { name: "Выбрать файл данных" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Выбрать файл ключей" })).toBeVisible();
    const demask = page.getByRole("button", { name: "Демаскировать" });
    await expect(demask).toBeVisible();
    await expect(demask).toHaveAttribute("aria-disabled", "true");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1]");
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page.getByRole("textbox", { name: "Ключи демаскировки" }).fill("Физлица:\n  - Иван");
    await expect(demask).not.toHaveAttribute("aria-disabled", "true");
});

test("пустые ключи блокируют демаскировку и не отправляют запрос", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1]");
    const demask = page.getByRole("button", { name: "Демаскировать" });
    await expect(demask).toHaveAttribute("aria-disabled", "true");
    const requests: string[] = [];
    page.on("request", (request) => requests.push(request.url()));
    // Keyboard activation and a forced DOM event must both be blocked by the
    // operation-boundary predicate, not by the visual disabled styling.
    await demask.press("Enter");
    await page.evaluate(() => {
        const button = [...document.querySelectorAll("button")].find((el) =>
            el.textContent?.includes("Демаскировать"),
        );
        button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await page.waitForTimeout(300);
    expect(requests.filter((url) => url.includes("unmask"))).toEqual([]);
});

test("на демаскировке выбранный файл данных даёт кнопку удаления", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    // Attach a .docx to the hidden data input.
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
    await expect(page.getByRole("button", { name: "Удалить файл" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Выбрать файл данных" })).toHaveCount(0);
});

test("замаскированный превью подсвечивает плейсхолдеры ровно один раз", async ({ page }) => {
    await page.goto("/");
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    // The masked preview is the second surface (the first is the source file).
    const surfaces = page.locator(".docx-viewer__surface");
    await expect(surfaces).toHaveCount(2);
    const maskedSurface = surfaces.nth(1);
    await expect(maskedSurface.locator("mark.docx-marker").first()).toBeVisible({ timeout: 30000 });

    const info = await maskedSurface.evaluate((surface) => {
        const text = (surface as HTMLElement).innerText;
        const count = (needle: string) => text.split(needle).length - 1;
        return {
            person: count("[PERSON_1]"),
            phone: count("[PHONE_1]"),
            marks: surface.querySelectorAll("mark.docx-marker").length,
        };
    });
    // Each marker once — the highlight must not duplicate them.
    expect(info.person).toBe(1);
    expect(info.phone).toBe(1);
    expect(info.marks).toBe(2);
});

test("невалидные ключи помечают поле и блокируют демаскировку", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1]");
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    const keysBox = page.getByRole("textbox", { name: "Ключи демаскировки" });
    await keysBox.fill("Неизвестный раздел:\n  - x");
    await expect(keysBox).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Некорректный формат.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Демаскировать" })).toHaveAttribute(
        "aria-disabled",
        "true",
    );
});

test("очистка данных на маскировке убирает сообщение об отсутствии фрагментов", async ({
    page,
}) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Просто текст без данных.");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(
        "Маскировка не выполнена: в тексте не нашлось чувствительной информации.",
    );
    await page.getByRole("textbox", { name: MASK_DATA }).fill("");
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue("");
});

test("tooltip маскировки показывает уникальные значения и число повторов", async ({ page }) => {
    await page.goto("/");
    await page
        .getByRole("textbox", { name: MASK_DATA })
        .fill("Иван Петров, Иван Петров, ivan@mail.ru, тел +7 900 123-45-67");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    const stats = page.locator(".editor-pane__stats");
    await expect(stats).toBeVisible({ timeout: 30000 });
    await stats.focus();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toContainText("Физлица:", { timeout: 10000 });
    await expect(tooltip).toContainText("с повторами");
    await expect(tooltip.locator("b").first()).toBeVisible();
});

test("tooltip демаскировки перечисляет неприменённые ключи", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1] пришёл.");
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill("Физлица:\n  - Иван Петров\nОрганизации:\n  - ООО «Рога и копыта»");
    const stats = page.locator(".editor-pane__stats");
    await expect(stats).toBeVisible({ timeout: 10000 });
    await stats.focus();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toContainText("Не применено 1:", { timeout: 10000 });
    await expect(tooltip).toContainText("- ООО «Рога и копыта»;");
});

test("демаскировка файла сообщает неприменённые ключи", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_MASKED_DOCX);
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    // The first two keys exist in the masked fixture; the third does not.
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill(
            "Физлица:\n  - Иван Петров\nНомера телефонов:\n  - '+7 900 123-45-67'\nОрганизации:\n  - ООО «Рога и копыта»",
        );
    await page.getByRole("button", { name: "Демаскировать" }).click();
    await expect(page.locator(".docx-viewer__surface mark.docx-marker").last()).toBeVisible({
        timeout: 30000,
    });

    const stats = page.locator(".editor-pane__stats");
    await expect(stats).toBeVisible({ timeout: 10000 });
    await stats.focus();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toContainText("Не применено 1:", { timeout: 10000 });
    await expect(tooltip).toContainText("- ООО «Рога и копыта»;");
});

test("статистика файла совпадает при маскировке и демаскировке с теми же ключами", async ({
    page,
}) => {
    await page.goto("/");
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    const maskStats = page.locator(".editor-pane__stats");
    await expect(maskStats).toBeVisible({ timeout: 30000 });
    const headline = (await maskStats.textContent())?.trim();
    expect(headline).toBeTruthy();

    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    const keys = await page.getByRole("textbox", { name: "Ключи демаскировки" }).inputValue();
    expect(keys).toContain("Физлица:");

    await switchMode(page, "Демаскировка");
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_MASKED_DOCX);
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page.getByRole("textbox", { name: "Ключи демаскировки" }).fill(keys);
    const demaskStats = page.locator(".editor-pane__stats");
    await expect(demaskStats).toBeVisible({ timeout: 15000 });
    expect((await demaskStats.textContent())?.trim()).toBe(headline);

    await demaskStats.focus();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip.locator("b").first()).toBeVisible({ timeout: 10000 });
    await expect(tooltip).toContainText("с повторами");
});

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

test("активное задание показывает «Отменить задание» вместо «Выбрать файл» и шлёт DELETE", async ({
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
    const cancel = page.getByRole("button", { name: "Отменить задание" });
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
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
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
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByText(/отчёт о результате/i)).toBeVisible({ timeout: 15000 });
});
