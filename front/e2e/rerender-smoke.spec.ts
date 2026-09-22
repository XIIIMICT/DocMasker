import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const SAMPLE_DOCX = fileURLToPath(new URL("./fixtures/sample.docx", import.meta.url));

test("editing keys does not re-render the restored preview", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Режим:/ }).hover();
    await page.getByRole("menuitemradio", { name: "Демаскировка", exact: true }).click();
    await page.setInputFiles('input[type="file"][accept*=".docx"]', SAMPLE_DOCX);
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill("Физлица:\n  - Иван Петров\nНомера телефонов:\n  - '+7 900 123-45-67'");
    await page.getByRole("button", { name: "Демаскировать" }).click();

    // The restored preview highlights the two restored values.
    const surface = page.locator(".docx-viewer__surface").last();
    await expect(surface.locator("mark.docx-marker")).toHaveCount(2, { timeout: 30000 });
    const before = await surface.locator("p").first().innerText();

    // Editing the keys must NOT re-render the preview: the marks stay the same
    // two and the paragraph text is unchanged.
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill(
            "Физлица:\n  - Иван Петров\nНомера телефонов:\n  - '+7 900 123-45-67'\nОрганизации:\n  - ООО «Тест»",
        );
    await page.waitForTimeout(500);
    await expect(surface.locator("mark.docx-marker")).toHaveCount(2);
    expect(await surface.locator("p").first().innerText()).toBe(before);
});
