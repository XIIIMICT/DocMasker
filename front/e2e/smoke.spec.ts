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
    await expect(page).toHaveTitle("ДокМаскер — Маскировка");
    await switchMode(page, "Демаскировка");
    await expect(page).toHaveTitle("ДокМаскер — Демаскировка");
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
    await expect(page.getByRole("tooltip")).toContainText("Физлица: 1 (1 уникальный)");
    await expect(page.getByRole("tooltip")).toContainText("Номера телефонов: 1 (1 уникальный)");
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

test("tooltip маскировки показывает уникальные счётчики по типам", async ({ page }) => {
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
    await expect(tooltip).toContainText("уникальн");
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
