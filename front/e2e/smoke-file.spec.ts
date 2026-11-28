import { expect, test } from "@playwright/test";

import { SAMPLE_DOCX, SAMPLE_MASKED_DOCX, switchMode } from "./_helpers";
import { DATA_FILE_INPUT } from "./helpers/selectors";

test("на демаскировке выбранный файл данных даёт кнопку удаления", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    // Attach a .docx to the hidden data input.
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
    await expect(page.getByRole("button", { name: "Удалить файл" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Выбрать файл данных" })).toHaveCount(0);
});

test("замаскированный превью подсвечивает плейсхолдеры ровно один раз", async ({ page }) => {
    await page.goto("/");
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
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

test("демаскировка файла сообщает неприменённые ключи", async ({ page }) => {
    await page.goto("/");
    await switchMode(page, "Демаскировка");
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_MASKED_DOCX);
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
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
    await page.getByRole("button", { name: "Замаскировать" }).click();
    const maskStats = page.locator(".editor-pane__stats");
    await expect(maskStats).toBeVisible({ timeout: 30000 });
    const headline = (await maskStats.textContent())?.trim();
    expect(headline).toBeTruthy();

    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    const keys = await page.getByRole("textbox", { name: "Ключи демаскировки" }).inputValue();
    expect(keys).toContain("Физлица:");

    await switchMode(page, "Демаскировка");
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_MASKED_DOCX);
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
