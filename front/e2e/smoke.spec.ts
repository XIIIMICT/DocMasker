import { expect, test } from "@playwright/test";

test("страница маскирования загружается с обоими полями", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("ДокМаскер")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Вводимый текст" })).toBeVisible();
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
    await page
        .getByRole("textbox", { name: "Вводимый текст" })
        .fill("Иван Петров, тел +7 900 123-45-67");
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
    await page.getByRole("textbox", { name: "Вводимый текст" }).fill("Просто текст без данных.");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(
        "Маскировка не выполнена: в тексте не нашлось чувствительной информации.",
    );
    await expect(page.getByRole("tab", { name: "Ключи демаскировки" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Скачать результат маскировки" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Скачать ключи демаскировки" })).toBeDisabled();
});
