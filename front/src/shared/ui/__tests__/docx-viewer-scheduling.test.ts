import { describe, expect, it } from "bun:test";

import { cancelHighlightUpdate, scheduleHighlightUpdate } from "@/shared/ui/docx-viewer";

const DELAY = 5;
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, DELAY + 30));

type TimerRef = { current: ReturnType<typeof setTimeout> | null };

function newTimer(): TimerRef {
    return { current: null };
}

describe("scheduleHighlightUpdate", () => {
    it("coalesces rapid key changes into one pass with the latest value", async () => {
        const timer = newTimer();
        let latest = "первый";
        const applied: string[] = [];
        const apply = () => applied.push(latest);

        scheduleHighlightUpdate(timer, apply, () => true, DELAY);
        latest = "второй";
        scheduleHighlightUpdate(timer, apply, () => true, DELAY);
        await settle();

        expect(applied).toEqual(["второй"]);
        expect(timer.current).toBeNull();
    });

    it("cancels scheduled work so a stale pass never runs", async () => {
        const timer = newTimer();
        let applied = 0;

        scheduleHighlightUpdate(
            timer,
            () => (applied += 1),
            () => true,
            DELAY,
        );
        cancelHighlightUpdate(timer);
        await settle();

        expect(applied).toBe(0);
        expect(timer.current).toBeNull();
    });

    it("drops work that became stale while it waited", async () => {
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
        await settle();

        expect(applied).toBe(0);
    });
});
