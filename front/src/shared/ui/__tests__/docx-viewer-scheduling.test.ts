import { afterEach, beforeEach, describe, expect, it, jest } from "bun:test";

import { cancelHighlightUpdate, scheduleHighlightUpdate } from "@/shared/ui/docx-viewer";

const DELAY = 5;
// Fake timers make the debounce boundary exact; no real-time settle window.
beforeEach(() => jest.useFakeTimers());
// Runs on success and on failure, so a failing assertion never leaks fake timers.
afterEach(() => jest.useRealTimers());

/** Cross the named debounce boundary in deterministic clock steps. */
const settle = () => jest.advanceTimersByTime(DELAY + 30);

type TimerRef = { current: ReturnType<typeof setTimeout> | null };

function newTimer(): TimerRef {
    return { current: null };
}

describe("scheduleHighlightUpdate", () => {
    it("coalesces rapid key changes into one pass with the latest value", () => {
        const timer = newTimer();
        let latest = "первый";
        const applied: string[] = [];
        const apply = () => applied.push(latest);

        scheduleHighlightUpdate(timer, apply, () => true, DELAY);
        latest = "второй";
        scheduleHighlightUpdate(timer, apply, () => true, DELAY);
        settle();

        expect(applied).toEqual(["второй"]);
        expect(timer.current).toBeNull();
    });

    it("cancels scheduled work so a stale pass never runs", () => {
        const timer = newTimer();
        let applied = 0;

        scheduleHighlightUpdate(
            timer,
            () => (applied += 1),
            () => true,
            DELAY,
        );
        cancelHighlightUpdate(timer);
        settle();

        expect(applied).toBe(0);
        expect(timer.current).toBeNull();
    });

    it("drops work that became stale while it waited", () => {
        const timer = newTimer();
        let current = true;
        let applied = 0;

        scheduleHighlightUpdate(
            timer,
            () => (applied += 1),
            () => current,
            DELAY,
        );
        current = false; // blob switched / generation bumped
        settle();

        expect(applied).toBe(0);
    });
});
