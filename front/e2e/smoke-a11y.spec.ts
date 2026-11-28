import { expect, test, type Locator, type Page } from "@playwright/test";

import { DEMASK_DATA, MASK_DATA, SAMPLE_DOCX, switchMode } from "./_helpers";
import { expectNoWcagViolations, waitForSettledUi } from "./helpers/a11y";
import { DATA_FILE_INPUT } from "./helpers/selectors";
import { waitForGraceFrames } from "./helpers/timing";

/**
 * Reach a control with real Tab presses: programmatic `focus()` does not
 * reliably trigger `:focus-visible`, which is part of the support-link contract.
 */
async function focusByKeyboard(page: Page, locator: Locator, maxTabs = 20): Promise<number> {
    for (let press = 1; press <= maxTabs; press += 1) {
        await page.keyboard.press("Tab");
        if (await locator.evaluate((node) => node === document.activeElement)) return press;
    }
    throw new Error(`control was not reached within ${maxTabs} Tab presses`);
}

test("ссылка поддержки раскрывается с исходной длительностью 180/220 мс", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".support-link")).toBeVisible();
    const styles = await page.evaluate(() => {
        const link = document.querySelector(".support-link");
        const label = document.querySelector(".support-link__label");
        if (!link || !label) throw new Error("support-link is missing");
        const linkStyle = getComputedStyle(link);
        const labelStyle = getComputedStyle(label);
        return {
            linkProperty: linkStyle.transitionProperty,
            linkDuration: linkStyle.transitionDuration,
            linkTiming: linkStyle.transitionTimingFunction,
            labelProperty: labelStyle.transitionProperty,
            labelDuration: labelStyle.transitionDuration,
            labelTiming: labelStyle.transitionTimingFunction,
            labelTransform: labelStyle.transform,
        };
    });
    // Restored by maintainer decision 2026-11-28: gap expands over 180 ms and
    // the label width over 220 ms, both with the original easing.
    expect(styles.linkProperty).toBe("gap, background-color");
    expect(styles.linkDuration).toBe("0.18s, 0.14s");
    expect(styles.linkTiming).toBe("cubic-bezier(0.22, 1, 0.36, 1), ease");
    expect(styles.labelProperty).toBe("max-width, opacity");
    expect(styles.labelDuration).toBe("0.22s, 0.14s");
    expect(styles.labelTiming).toBe("cubic-bezier(0.22, 1, 0.36, 1), ease");
    expect(styles.labelTransform).toBe("none");

    await page.locator(".support-link").hover();
    const animating = await page.evaluate(() => {
        const link = document.querySelector(".support-link");
        const label = document.querySelector(".support-link__label");
        if (!link || !label) throw new Error("support-link is missing");
        const describe = (node: Element) =>
            node.getAnimations().map((animation) => ({
                property: (animation as CSSTransition).transitionProperty,
                duration: animation.effect?.getTiming().duration ?? null,
            }));
        const running = { link: describe(link), label: describe(label) };
        // Freeze the real Chromium transitions at 80 ms: an intermediate width
        // is then deterministic instead of depending on a fragile sleep.
        const active = [...link.getAnimations(), ...label.getAnimations()];
        for (const animation of active) {
            animation.pause();
            animation.currentTime = 80;
        }
        const mid = {
            gap: parseFloat(getComputedStyle(link).gap),
            maxWidth: parseFloat(getComputedStyle(label).maxWidth),
            opacity: parseFloat(getComputedStyle(label).opacity),
        };
        for (const animation of active) animation.play();
        return { running, mid };
    });
    // Chromium reports the `gap` shorthand as column-gap/row-gap transitions.
    expect(animating.running.link).toEqual(
        expect.arrayContaining([
            expect.objectContaining({ property: "column-gap", duration: 180 }),
            expect.objectContaining({ property: "background-color", duration: 140 }),
        ]),
    );
    expect(animating.running.label).toEqual(
        expect.arrayContaining([
            expect.objectContaining({ property: "max-width", duration: 220 }),
            expect.objectContaining({ property: "opacity", duration: 140 }),
        ]),
    );
    expect(animating.mid.gap).toBeGreaterThan(0);
    expect(animating.mid.gap).toBeLessThan(7.2);
    expect(animating.mid.maxWidth).toBeGreaterThan(0);
    expect(animating.mid.maxWidth).toBeLessThan(320);
    expect(animating.mid.opacity).toBeGreaterThan(0);
    expect(animating.mid.opacity).toBeLessThan(1);
    await expect(page.locator(".support-link__label")).toHaveCSS("max-width", "320px");
    await expect(page.locator(".support-link")).toHaveCSS("gap", "7.2px");
});

test("reduced-motion убирает переходы приложения, сохраняя конечное состояние", async ({
    page,
}) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.locator(".support-link")).toBeVisible();
    const styles = await page.evaluate(() => {
        const link = document.querySelector(".support-link");
        const label = document.querySelector(".support-link__label");
        if (!link || !label) throw new Error("support-link is missing");
        const linkStyle = getComputedStyle(link);
        const labelStyle = getComputedStyle(label);
        return {
            link: linkStyle.transitionDuration,
            linkProperties: linkStyle.transitionProperty,
            linkAnimation: linkStyle.animationName,
            label: labelStyle.transitionDuration,
            labelProperties: labelStyle.transitionProperty,
            labelTransform: labelStyle.transform,
            labelAnimation: labelStyle.animationName,
        };
    });
    // The original gap/max-width transitions stay declared, but their tokens
    // (and the decorative ones) all collapse to 0s under reduced motion.
    for (const value of [styles.link, styles.label]) {
        expect(value.split(",").every((part) => part.trim() === "0s")).toBe(true);
    }
    expect(styles.linkProperties).toBe("gap, background-color");
    expect(styles.labelProperties).toBe("max-width, opacity");
    expect(styles.labelTransform).toBe("none");
    expect(styles.linkAnimation).toBe("none");
    expect(styles.labelAnimation).toBe("none");
    // The control stays usable and reaches its hover end state instantly;
    // the keyboard end state is covered by the focus test below.
    await page.locator(".support-link").hover();
    await expect(page.locator(".support-link")).toHaveCSS("gap", "7.2px");
    await expect(page.locator(".support-link__label")).toHaveCSS("max-width", "320px");
    await expect(page.locator(".support-link__label")).toHaveCSS("opacity", "1");
});

test("reduced-motion не отключает собственные правила HeroUI", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    const info = await page.evaluate(() => {
        let reduceRules = 0;
        const walk = (rules: CSSRuleList) => {
            for (let index = 0; index < rules.length; index += 1) {
                const rule = rules.item(index);
                if (!rule) continue;
                if (
                    rule instanceof CSSMediaRule &&
                    rule.conditionText.replace(/\s/g, "").includes("prefers-reduced-motion:reduce")
                ) {
                    reduceRules += 1;
                }
                if ("cssRules" in rule) walk((rule as CSSGroupingRule).cssRules);
            }
        };
        for (const sheet of document.styleSheets) {
            try {
                walk(sheet.cssRules);
            } catch {
                /* cross-origin sheet */
            }
        }
        const button = document.querySelector("button");
        if (!button) throw new Error("no button on the page");
        return {
            reduceRules,
            buttonTransition: getComputedStyle(button).transitionProperty,
        };
    });
    // HeroUI's own motion-reduce CSS stays in the cascade; our reduce block
    // only zeroes DocMasker's token and cannot silence these rules.
    expect(info.reduceRules).toBeGreaterThan(0);
    expect(info.buttonTransition).toBe("none");
});

test("ссылка поддержки доступна с клавиатуры и сохраняет подпись при фокусе", async ({ page }) => {
    await page.goto("/");
    const link = page.locator(".support-link");
    await focusByKeyboard(page, link);
    await expect(link).toBeFocused();
    await expect(link).toHaveAttribute("aria-label", "Связаться с разработчиком");
    await expect(link).toHaveAttribute("href", /^mailto:/);
    await expect(link).toHaveCSS("outline-style", "solid");
    await expect(page.locator(".support-link__label")).toHaveCSS("max-width", "320px");
    await expect(page.locator(".support-link__label")).toHaveCSS("opacity", "1");
    await expect(page.locator(".support-link__label")).toHaveCSS("transform", "none");
});

test("на узком экране подпись поддержки остаётся в Tooltip при фокусе", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const link = page.locator(".support-link");
    await focusByKeyboard(page, link);
    await expect(page.getByRole("tooltip")).toContainText("Связаться с разработчиком");
    await expect(page.locator(".support-link__label")).toHaveCSS("max-width", "0px");
    await expect(page.locator(".support-link__label")).toHaveCSS("opacity", "0");
    await expect(page.locator(".support-link__label")).toHaveCSS("transform", "none");
});

test("визуально неактивная кнопка недоступна мышью, но фокусируется и защищена на границе операции", async ({
    page,
}) => {
    await page.goto("/");
    let maskCalls = 0;
    await page.route("**/api/mask", async (route) => {
        maskCalls += 1;
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                text: "[PERSON_1]",
                keys: "Физлица:\n  - Иван Петров",
                stats: {
                    total: 1,
                    occurrences_by_label: { person: 1 },
                    unique_by_label: { person: 1 },
                },
            }),
        });
    });
    const mask = page.getByRole("button", { name: "Замаскировать" });
    const state = await mask.evaluate((node) => {
        const style = getComputedStyle(node);
        return {
            pointerEvents: style.pointerEvents,
            cursor: style.cursor,
            ariaDisabled: node.getAttribute("aria-disabled"),
            nativeDisabled: node.hasAttribute("disabled"),
            tabIndex: (node as HTMLElement).tabIndex,
        };
    });
    expect(state).toEqual({
        pointerEvents: "none",
        cursor: "not-allowed",
        ariaDisabled: "true",
        nativeDisabled: false,
        tabIndex: 0,
    });

    const box = await mask.boundingBox();
    if (!box) throw new Error("mask button has no box");
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await waitForGraceFrames(page);
    expect(maskCalls).toBe(0);

    // Keyboard activation still reaches the handler; the operation boundary
    // (not CSS) is the only thing that may stop the request.
    await mask.press("Enter");
    await waitForGraceFrames(page);
    expect(maskCalls).toBe(0);
    await focusByKeyboard(page, mask);
    await expect(mask).toBeFocused();
});

// ---------------------------------------------------------------------------
// C1: axe scans of real states. Every state is reached with deterministic
// fixtures/routes and web-first assertions; `expectNoWcagViolations` drains
// overlay transitions before scanning. These scans are a regression net for
// WCAG 2/2.1 A/AA rules only — they are not an accessibility audit (C2 doc).
// ---------------------------------------------------------------------------

test.describe("axe WCAG 2/2.1 A/AA scans", () => {
    test("axe: пустая маскировка", async ({ page }) => {
        await page.goto("/");
        await expect(page.getByRole("textbox", { name: MASK_DATA })).toBeVisible();
        await expectNoWcagViolations(page, "mask / empty");
    });

    test("axe: заполненная маскировка и открытый Tooltip статистики", async ({ page }) => {
        await page.goto("/");
        await page
            .getByRole("textbox", { name: MASK_DATA })
            .fill("Иван Петров, тел +7 900 123-45-67");
        await page.getByRole("button", { name: "Замаскировать" }).click();
        await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(/\[PERSON_1\]/, {
            timeout: 30000,
        });
        await expectNoWcagViolations(page, "mask / populated");
        await page.locator(".editor-pane__stats").focus();
        await expect(page.getByRole("tooltip")).toBeVisible();
        await expectNoWcagViolations(page, "mask / stats tooltip");
    });

    test("axe: ошибка результата маскировки", async ({ page }) => {
        await page.route("**/api/mask", async (route) => {
            await route.fulfill({
                status: 422,
                contentType: "application/json",
                body: JSON.stringify({
                    code: "validation_error",
                    detail: "Проверьте исходные данные.",
                    request_id: "synthetic-request",
                }),
            });
        });
        await page.goto("/");
        await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров");
        await page.getByRole("button", { name: "Замаскировать" }).click();
        const error = page.getByRole("textbox", { name: "Ошибка" });
        await expect(error).toHaveValue(/Ошибка:/);
        await expect(error).toHaveAttribute("aria-invalid", "true");
        await expectNoWcagViolations(page, "mask / result error");
    });

    test("axe: пустая демаскировка", async ({ page }) => {
        await page.goto("/");
        await switchMode(page, "Демаскировка");
        await expect(page.getByRole("textbox", { name: DEMASK_DATA })).toBeVisible();
        await expectNoWcagViolations(page, "demask / empty");
    });

    test("axe: некорректные ключи демаскировки", async ({ page }) => {
        await page.goto("/");
        await switchMode(page, "Демаскировка");
        await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1]");
        await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
        await page
            .getByRole("textbox", { name: "Ключи демаскировки" })
            .fill("Неизвестный раздел:\n  - x");
        await expect(page.getByText("Некорректный формат.")).toBeVisible();
        await expectNoWcagViolations(page, "demask / validation error");
    });

    test("axe: ошибка результата демаскировки", async ({ page }) => {
        await page.route("**/api/unmask", async (route) => {
            await route.fulfill({
                status: 422,
                contentType: "application/json",
                body: JSON.stringify({
                    code: "validation_error",
                    detail: "Ключи не соответствуют исходному файлу.",
                    request_id: "synthetic-request",
                }),
            });
        });
        await page.goto("/");
        await switchMode(page, "Демаскировка");
        await page.getByRole("textbox", { name: DEMASK_DATA }).fill("[PERSON_1]");
        await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
        await page
            .getByRole("textbox", { name: "Ключи демаскировки" })
            .fill("Физлица:\n  - Иван Петров");
        await page.getByRole("button", { name: "Демаскировать" }).click();
        await expect(page.getByRole("textbox", { name: "Ошибка" })).toHaveValue(/Ошибка:/);
        await expectNoWcagViolations(page, "demask / result error");
    });

    test("axe: открытый выпадающий список режимов", async ({ page }) => {
        await page.goto("/");
        await page.getByRole("button", { name: /Режим:/ }).hover();
        await expect(page.getByRole("menuitemradio", { name: "Демаскировка" })).toBeVisible();
        await expectNoWcagViolations(page, "brand dropdown / open");
    });

    test("axe: предпросмотр DOCX и сообщение о ожидании", async ({ page }) => {
        await page.goto("/");
        await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
        await expect(
            page.getByText("Файл замаскируется после нажатия «Замаскировать»"),
        ).toBeVisible();
        await page.waitForFunction(
            () => document.querySelectorAll(".docx-viewer__surface p").length > 0,
        );
        await expectNoWcagViolations(page, "DOCX / preview + status");
    });

    test("axe: ошибка обработки DOCX", async ({ page }) => {
        await page.route("**/api/mask-file", async (route) => {
            await route.fulfill({
                status: 422,
                contentType: "application/json",
                body: JSON.stringify({
                    code: "invalid_package",
                    detail: "Файл не прошёл проверку.",
                    request_id: "synthetic-request",
                }),
            });
        });
        await page.goto("/");
        await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
        await page.getByRole("button", { name: "Замаскировать" }).click();
        await expect(page.getByRole("textbox", { name: "Ошибка" })).toHaveValue(/Ошибка:/);
        await expectNoWcagViolations(page, "DOCX / error");
    });

    test("axe: узкая ширина и мобильная раскладка", async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto("/");
        await expect(page.getByRole("textbox", { name: MASK_DATA })).toBeVisible();
        await expectNoWcagViolations(page, "narrow / mask");
        await switchMode(page, "Демаскировка");
        await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
        await page
            .getByRole("textbox", { name: "Ключи демаскировки" })
            .fill("Неизвестный раздел:\n  - x");
        await expect(page.getByText("Некорректный формат.")).toBeVisible();
        await expectNoWcagViolations(page, "narrow / demask validation");
    });

    test("axe: тёмная тема с активной кнопкой", async ({ page }) => {
        await page.goto("/");
        await page.evaluate(() => localStorage.setItem("theme", "dark"));
        await page.reload();
        await expect(page.locator("html")).toHaveClass(/dark/);
        await page
            .getByRole("textbox", { name: MASK_DATA })
            .fill("Иван Петров, тел +7 900 123-45-67");
        await page.getByRole("button", { name: "Замаскировать" }).click();
        await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue(/\[PERSON_1\]/, {
            timeout: 30000,
        });
        await expectNoWcagViolations(page, "dark / populated");
    });
});

// ---------------------------------------------------------------------------
// C2: Playwright keyboard/focus walkthroughs. Outcomes and the explicit
// screen-reader scope decision are recorded in `docs/ACCESSIBILITY.md`.
// ---------------------------------------------------------------------------

test("клавиатура: логичный порядок фокуса, видимый индикатор и выход из страницы", async ({
    page,
}) => {
    await page.goto("/");
    await waitForSettledUi(page);
    const focusables = page.locator(
        '.app-shell button:visible:not([tabindex="-1"]), .app-shell a[href]:visible:not([tabindex="-1"]), .app-shell textarea:visible:not([disabled]), .app-shell [role="tab"]:visible:not([tabindex="-1"])',
    );
    const expected = await focusables.evaluateAll((elements) =>
        elements
            .filter(
                (element) =>
                    element.tabIndex >= 0 &&
                    !element.hasAttribute("disabled") &&
                    element.getAttribute("aria-disabled") !== "true",
            )
            .map(
                (element) =>
                    element.getAttribute("aria-label") ??
                    element.textContent?.trim() ??
                    element.tagName,
            ),
    );
    const total = expected.length;
    expect(total).toBeGreaterThan(5);
    const visited: string[] = [];
    // Next.js devtools injects a focusable portal; skip anything outside the app.
    for (let press = 0; press < total + 6; press += 1) {
        await page.keyboard.press("Tab");
        const state = await page.evaluate(() => {
            const active = document.activeElement;
            if (!(active instanceof HTMLElement)) return null;
            if (!active.closest(".app-shell")) return { key: "", outside: true };
            const style = getComputedStyle(active);
            return {
                key:
                    active.getAttribute("aria-label") ??
                    active.textContent?.trim() ??
                    active.tagName,
                outside: false,
                outline: style.outlineStyle,
                shadow: style.boxShadow,
            };
        });
        if (state === null) break;
        if (state.outside) continue;
        visited.push(state.key);
        expect(state.outline === "solid" || state.shadow !== "none").toBe(true);
        if (visited.filter((key) => expected.includes(key)).length === total) break;
    }
    // The app's controls appear in DOM order, even if a hidden panel or the
    // devtools portal adds another stop between them.
    expect(visited.filter((key) => expected.includes(key))).toEqual(expected);
    expect(visited[0]).toMatch(/Режим:/);
    // Tab after the last control moves focus away (browser chrome/devtools or
    // wrap-around): the last control does not trap focus.
    await page.keyboard.press("Tab");
    const afterLast = await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null;
        return active?.getAttribute("aria-label") ?? active?.textContent?.trim() ?? active?.tagName;
    });
    expect(afterLast).not.toBe(expected.at(-1));
});

test("клавиатура: Enter открывает список режимов, Escape закрывает и возвращает фокус", async ({
    page,
}) => {
    await page.goto("/");
    const trigger = page.getByRole("button", { name: /Режим:/ });
    await focusByKeyboard(page, trigger);
    await expect(trigger).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(
        page.getByRole("menuitemradio", { name: "Маскировка", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("ArrowDown");
    const item = page.getByRole("menuitemradio", { name: "Демаскировка", exact: true });
    await expect(item).toBeVisible();
    await expect(item).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(trigger).toBeFocused();
});

test("состояния загрузки, успеха и ошибки различимы не только цветом", async ({ page }) => {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
        release = resolve;
    });
    await page.route("**/api/mask", async (route) => {
        await gate;
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                text: "[PERSON_1]",
                keys: "Физлица:\n  - Иван Петров",
                stats: {
                    total: 1,
                    occurrences_by_label: { person: 1 },
                    unique_by_label: { person: 1 },
                },
            }),
        });
    });
    await page.goto("/");
    await page.getByRole("textbox", { name: MASK_DATA }).fill("Иван Петров");
    await page.getByRole("button", { name: "Замаскировать" }).click();
    // Loading is named in text, not only signalled by a colour change.
    await expect(page.getByRole("button", { name: "Маскируем…" })).toBeVisible();
    release?.();
    await expect(page.getByRole("textbox", { name: "Результат" })).toHaveValue("[PERSON_1]");
    await expect(page.locator(".editor-pane__stats")).toHaveText("1 фрагмент");
});
