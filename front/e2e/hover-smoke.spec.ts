import { expect, test, type Locator, type Page } from "@playwright/test";

import { waitForCloseGrace } from "./helpers/timing";

// HoverDropdown regression coverage: the brand's mode menu opens on React Aria
// hover/press and closes only after the pointer has been outside both the
// trigger and the modal popover for the 250 ms grace period. Direct mouseleave
// closing is unsafe because react-aria makes the page inert while the popover
// is open, which produces a phantom pointerout on the trigger.

const TRIGGER = /Режим:/;
/** Dropdown.Popover keeps this class from the trigger's `className` prop. */
const POPOVER_CLASS = ".brand-popover";
/**
 * Flicker sampling, deliberately shorter than the 250 ms close grace: the
 * pointer must re-enter *before* the grace expires. This timing is the
 * behaviour under test, so it is not replaced by the grace helper.
 */
const REENTRY_BEFORE_GRACE_MS = 80;

async function triggerButton(page: Page): Promise<Locator> {
    const btn = page.getByRole("button", { name: TRIGGER });
    await expect(btn).toBeVisible({ timeout: 15000 });
    return btn;
}

async function menu(page: Page): Promise<Locator> {
    return page.locator(POPOVER_CLASS);
}

async function centerOf(locator: Locator) {
    const box = await locator.boundingBox();
    if (!box) throw new Error("no bounding box");
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function hoverWithRealMouse(page: Page, locator: Locator) {
    const center = await centerOf(locator);
    await page.mouse.move(center.x - 70, center.y);
    await page.mouse.move(center.x - 35, center.y);
    await page.mouse.move(center.x - 10, center.y);
    await page.mouse.move(center.x, center.y);
}

async function moveAway(page: Page) {
    await page.mouse.move(5, 5);
    await page.mouse.move(3, 3);
}

async function openByHover(page: Page) {
    await page.goto("/");
    const btn = await triggerButton(page);
    await hoverWithRealMouse(page, btn);
    const popup = await menu(page);
    await expect(popup).toBeVisible({ timeout: 5000 });
    return { btn, popup };
}

/**
 * Geometry events only — the pointer itself never moves. `scroll` is sent from
 * a descendant as well as the window so the capture listener is exercised in
 * both the ancestor and target phases (element scroll events do not bubble).
 */
async function fireGeometryEvents(page: Page) {
    await page.evaluate(() => {
        document.body.dispatchEvent(new Event("scroll"));
        window.dispatchEvent(new Event("scroll"));
        window.dispatchEvent(new Event("resize"));
    });
}

test("a real hover opens the brand menu", async ({ page }) => {
    const { popup } = await openByHover(page);
    await expect(popup).toBeVisible();
});

test("moving from the trigger into the popover keeps the menu open", async ({ page }) => {
    const { btn, popup } = await openByHover(page);
    const from = await centerOf(btn);
    const to = await centerOf(popup);
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2);
    await page.mouse.move(to.x, to.y);
    await waitForCloseGrace(page);
    await expect(popup).toBeVisible();
});

test("outside pointer closes only after the grace period", async ({ page }) => {
    const { popup } = await openByHover(page);
    await moveAway(page);
    await expect(popup).toBeVisible();
    await expect(popup).toHaveCount(0, { timeout: 2000 });
});

test("re-entering before the grace period cancels the close", async ({ page }) => {
    const { btn, popup } = await openByHover(page);
    await moveAway(page);
    await page.waitForTimeout(REENTRY_BEFORE_GRACE_MS);
    const trigger = await centerOf(btn);
    await page.mouse.move(trigger.x, trigger.y);
    await waitForCloseGrace(page);
    await expect(popup).toBeVisible();
});

test("Escape closes the menu and hover stays suppressed until re-entry", async ({ page }) => {
    const { btn, popup } = await openByHover(page);
    await page.keyboard.press("Escape");
    await expect(popup).toHaveCount(0, { timeout: 3000 });
    await waitForCloseGrace(page);
    await expect(popup).toHaveCount(0);
    await moveAway(page);
    await hoverWithRealMouse(page, btn);
    await expect(popup).toBeVisible({ timeout: 5000 });
});

test("keyboard opening is not closed by a stale pointer position", async ({ page }) => {
    await page.goto("/");
    const btn = await triggerButton(page);
    // A stale pointer position is enough: no real mouse movement is needed.
    await page.evaluate(() =>
        window.dispatchEvent(new PointerEvent("pointermove", { clientX: 5, clientY: 5 })),
    );
    await btn.press("Enter");
    const popup = await menu(page);
    await expect(popup).toBeVisible({ timeout: 5000 });
    await waitForCloseGrace(page);
    await expect(popup).toBeVisible();
});

test("a rapid reopen is not closed by a timer from the previous interaction", async ({ page }) => {
    const { popup } = await openByHover(page);
    await moveAway(page);
    await expect(popup).toHaveCount(0, { timeout: 2000 });
    await openByHover(page);
    await waitForCloseGrace(page);
    await expect(popup).toBeVisible();
});

test("scroll and resize with the pointer inside the popover keep the menu open", async ({
    page,
}) => {
    const { btn, popup } = await openByHover(page);
    const from = await centerOf(btn);
    const to = await centerOf(popup);
    await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2);
    await page.mouse.move(to.x, to.y);

    // The pointer stays still: the same coalesced check must re-read the rects
    // instead of closing on a decision cached when the pointer last moved.
    await fireGeometryEvents(page);
    await waitForCloseGrace(page);
    await expect(popup).toBeVisible();
});

// The opposite direction (a geometry change that makes the stationary pointer
// fall outside both rectangles) is covered by the deterministic unit tests on
// `listenForGeometryChanges`: HeroUI's floating positioning rewrites the
// popover's inline transform, so moving it from an e2e test is not reliable.

test("Escape suppression holds when scroll and resize events fire", async ({ page }) => {
    const { btn, popup } = await openByHover(page);
    await page.keyboard.press("Escape");
    await expect(popup).toHaveCount(0, { timeout: 3000 });

    // Scroll/resize must not re-open a menu closed by Escape: pointer-open is
    // no longer armed and hover suppression is still set.
    await fireGeometryEvents(page);
    await waitForCloseGrace(page);
    await expect(popup).toHaveCount(0);

    // Suppression lifts only after the pointer leaves the trigger.
    await moveAway(page);
    await hoverWithRealMouse(page, btn);
    await expect(popup).toBeVisible({ timeout: 5000 });
});

test.describe("touch interaction", () => {
    test.use({ hasTouch: true });

    test("press opens the menu without a synthetic-hover close", async ({ page }) => {
        await page.goto("/");
        const btn = await triggerButton(page);
        const point = await centerOf(btn);
        await page.touchscreen.tap(point.x, point.y);
        const popup = await menu(page);
        await expect(popup).toBeVisible({ timeout: 5000 });
        await waitForCloseGrace(page);
        await expect(popup).toBeVisible();
    });
});
