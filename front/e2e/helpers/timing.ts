import type { Page } from "@playwright/test";

/**
 * Two animation frames: a deterministic observation window, not a duration
 * guess. One frame flushes the event handler and its microtasks, the second
 * flushes a state commit that schedules work from that handler. Assertions
 * (route counters, node identity, web-first matchers) remain the evidence.
 */
export const GRACE_FRAMES = 2;

export async function waitForGraceFrames(page: Page, frames = GRACE_FRAMES): Promise<void> {
    await page.evaluate(async (count) => {
        for (let index = 0; index < count; index += 1) {
            await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        }
    }, frames);
}

/**
 * Mirrors `hover-dropdown.tsx::CLOSE_GRACE_MS` (250 ms). The extra margin only
 * covers scheduling jitter; the helper is the *named* grace period that every
 * "menu stays open / stays closed" assertion must outlast. It is deliberately
 * not used for flicker sampling — re-entering *before* the grace expires is
 * the behaviour under test there and uses its own, shorter named delay.
 */
export const CLOSE_GRACE_MS = 250;
const GRACE_MARGIN_MS = 50;

export const CLOSE_GRACE_OBSERVATION_MS = CLOSE_GRACE_MS + GRACE_MARGIN_MS;

export async function waitForCloseGrace(page: Page): Promise<void> {
    await page.waitForTimeout(CLOSE_GRACE_OBSERVATION_MS);
}
