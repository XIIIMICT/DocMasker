import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

/**
 * Explicit ruleset for every scan in this suite. `withTags` selects rules
 * tagged with any of these axe tags; it never implies "accessible" — axe has
 * no layout/point-of-view model and cannot replace the manual keyboard review
 * recorded in `docs/ACCESSIBILITY.md`.
 */
export const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] as const;

/**
 * Wait for a settled UI instead of sleeping. Web-first assertions in each test
 * establish the state; this only drains the short React Aria popover/tooltip
 * transitions so axe does not sample a half-transparent frame.
 */
export async function waitForSettledUi(page: Page): Promise<void> {
    await page.locator(".app-shell").waitFor({ state: "visible" });
    await page.waitForFunction(
        () => document.getAnimations().every((animation) => animation.playState !== "running"),
        undefined,
        { timeout: 10_000 },
    );
}

/**
 * Scan the whole document (portals included) with the explicit WCAG ruleset.
 * Failures list the rule, impact and targets so a regression is diagnosable
 * from the Playwright report alone. No rule is disabled or filtered here.
 */
export async function expectNoWcagViolations(page: Page, state: string): Promise<void> {
    await waitForSettledUi(page);
    const { violations } = await new AxeBuilder({ page }).withTags([...WCAG_TAGS]).analyze();
    const findings = violations.map((violation) => ({
        rule: violation.id,
        impact: violation.impact,
        help: violation.help,
        targets: violation.nodes.map((node) => node.target.join(" ")),
    }));
    expect(findings, `${state}: axe WCAG 2/2.1 A/AA findings`).toEqual([]);
}
