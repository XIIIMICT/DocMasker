import { expect, test, type Page } from "@playwright/test";

const MASK_DATA = "Данные для маскировки";
const DEMASK_DATA = "Данные для демаскировки";

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
    await stats.hover();
    await expect(page.getByRole("tooltip")).toContainText("1 физлицо + 1 телефон");
    // The keys tab carries the demasking YAML; both downloads become available.
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await expect(page.getByRole("textbox", { name: "Ключи демаскировки" })).toHaveValue(
        /Физлица:\s*\n\s+- Иван Петров/,
    );
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
