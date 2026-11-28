import { expect, test } from "@playwright/test";

import { DEMASK_DATA, MASK_DATA, switchMode } from "./_helpers";
import { waitForGraceFrames } from "./helpers/timing";

test("смена темы мгновенная: CSS-переход html не используется", async ({ page }) => {
    await page.goto("/");
    await page.evaluate(() => localStorage.setItem("theme", "light"));
    await page.reload();
    const read = () =>
        page.evaluate(() => {
            const html = document.documentElement;
            return {
                transition: getComputedStyle(html).transitionDuration,
                className: html.className,
                color: getComputedStyle(html).color,
            };
        });
    const before = await read();
    expect(before.transition).toBe("0s");
    await page.getByRole("tab", { name: "Тёмная" }).click();
    // next-themes updates <html> after the click; wait for the committed class
    // before sampling computed styles, otherwise this is a timing flake.
    await expect(page.locator("html")).toHaveClass(/dark/);
    const after = await read();
    expect(after.transition).toBe("0s");
    expect(after.className).toContain("dark");
    expect(after.color).toBe("rgb(255, 255, 255)");
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
        const firstActions = actions[0];
        const firstPane = panes[0];
        const secondLabel = labels[1];
        if (!firstActions || !firstPane || !secondLabel) {
            throw new Error("expected two panes with actions and a second label");
        }
        return {
            buttonsInsideFirstPane: firstActions.bottom <= firstPane.bottom + 1,
            buttonsBeforeNextLabel: firstActions.bottom <= secondLabel.top + 1,
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
    // One initial-render contract: the brand, both panes and the action are
    // present immediately, while the key/download affordances stay gated.
    await expect(page.getByText("ДокМаскер")).toBeVisible();
    await expect(page.getByRole("textbox", { name: MASK_DATA })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Результат маскировки" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Замаскировать" })).toBeVisible();
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
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(/\[PERSON_1\]/, {
        timeout: 30000,
    });
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
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(/\[LINK_1\]/, {
        timeout: 30000,
    });
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
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(/\[PERSON_1\]/, {
        timeout: 30000,
    });
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

    // A route counter is the evidence: every unmask attempt passes through
    // this route, including one scheduled after the gesture. No fixed sleep.
    const unmaskRequests: string[] = [];
    await page.route("**/api/unmask", async (route) => {
        unmaskRequests.push(`${route.request().method()} ${route.request().url()}`);
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ text: "Иван Петров" }),
        });
    });

    // Keyboard activation and a forced DOM event must both be blocked by the
    // operation-boundary predicate, not by the visual disabled styling.
    await demask.press("Enter");
    await page.evaluate(() => {
        const button = [...document.querySelectorAll("button")].find((el) =>
            el.textContent?.includes("Демаскировать"),
        );
        button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    // Explicit grace: flush the gesture's task and the following microtasks.
    await waitForGraceFrames(page);

    // Synchronisation barrier: one *valid* demask through the same endpoint
    // must complete. Any request caused by either illegal activation would
    // already be counted by then, so the final count is exact.
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill("Физлица:\n  - Иван Петров");
    await expect(demask).not.toHaveAttribute("aria-disabled", "true");
    await demask.click();
    await expect(page.getByRole("textbox", { name: "Результат демаскировки" })).toHaveValue(
        "Иван Петров",
    );
    expect(unmaskRequests).toHaveLength(1);
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
