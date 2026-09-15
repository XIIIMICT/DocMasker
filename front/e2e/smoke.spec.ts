import { expect, test } from "@playwright/test";

test("страница маскирования загружается с обоими полями", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("ДокМаскер");
    await expect(page.getByRole("textbox").first()).toBeVisible();
    await expect(page.getByText("Вводимый текст")).toBeVisible();
    await expect(page.getByText("Замаскированный текст")).toBeVisible();
    await expect(page.getByRole("button", { name: "Замаскировать" })).toBeVisible();
});

test("текст с PII маскируется через /api/mask", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("textbox").first();
    await input.fill("Иван Петров, тел +7 900 123-45-67");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    // The masked result lands in the second text area, with a summary in the label.
    await expect
        .poll(async () => page.getByRole("textbox").nth(1).inputValue(), { timeout: 30000 })
        .toContain("[PERSON]");
    // The result label carries the breakdown plus the total, two lines.
    await expect(page.getByText(/1 физлицо \+ 1 телефон/)).toBeVisible();
    await expect(page.getByText(/= 2 фрагмента всего/)).toBeVisible();
    // Text results are downloadable as .txt regardless of the input source.
    await expect(page.getByRole("button", { name: "Скачать файл" })).toBeVisible();
});
