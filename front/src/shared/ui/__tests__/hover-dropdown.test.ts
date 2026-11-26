import { describe, expect, it, spyOn } from "bun:test";

import { listenForGeometryChanges, pointInRect } from "@/shared/ui/hover-dropdown";

type Bounds = { left: number; right: number; top: number; bottom: number };

function fakeElement(bounds: Bounds): HTMLElement {
    return { getBoundingClientRect: () => bounds } as HTMLElement;
}

describe("pointInRect", () => {
    const bounds: Bounds = { left: 10, right: 110, top: 20, bottom: 80 };
    const element = fakeElement(bounds);

    it("accepts a point inside the rectangle", () => {
        expect(pointInRect({ x: 60, y: 50 }, element)).toBe(true);
    });

    it("treats the edges as inside (inclusive bounds)", () => {
        expect(pointInRect({ x: 10, y: 20 }, element)).toBe(true);
        expect(pointInRect({ x: 110, y: 80 }, element)).toBe(true);
    });

    it("rejects points outside the rectangle", () => {
        expect(pointInRect({ x: 9, y: 50 }, element)).toBe(false);
        expect(pointInRect({ x: 111, y: 50 }, element)).toBe(false);
        expect(pointInRect({ x: 60, y: 19 }, element)).toBe(false);
        expect(pointInRect({ x: 60, y: 81 }, element)).toBe(false);
    });

    it("rejects a missing element", () => {
        expect(pointInRect({ x: 60, y: 50 }, null)).toBe(false);
    });
});

describe("listenForGeometryChanges", () => {
    it("adds a capture scroll listener and a resize listener, then removes both", () => {
        const addSpy = spyOn(window, "addEventListener");
        const removeSpy = spyOn(window, "removeEventListener");
        try {
            const dispose = listenForGeometryChanges(
                () => undefined,
                () => true,
            );
            expect(addSpy).toHaveBeenCalledTimes(2);
            expect(addSpy.mock.calls[0]?.[0]).toBe("scroll");
            expect(addSpy.mock.calls[0]?.[2]).toBe(true);
            expect(addSpy.mock.calls[1]?.[0]).toBe("resize");

            dispose();
            expect(removeSpy).toHaveBeenCalledTimes(2);
            expect(removeSpy.mock.calls[0]?.[0]).toBe("scroll");
            expect(removeSpy.mock.calls[0]?.[2]).toBe(true);
            expect(removeSpy.mock.calls[1]?.[0]).toBe("resize");
            expect(removeSpy.mock.calls[1]?.[2]).toBeUndefined();
        } finally {
            addSpy.mockRestore();
            removeSpy.mockRestore();
        }
    });

    it("schedules only while pointer-open and stops after dispose", () => {
        let pointerOpen = false;
        let scheduled = 0;
        const dispose = listenForGeometryChanges(
            () => {
                scheduled += 1;
            },
            () => pointerOpen,
        );
        const scroll = () => window.dispatchEvent(new window.Event("scroll"));
        const resize = () => window.dispatchEvent(new window.Event("resize"));

        scroll();
        expect(scheduled).toBe(0);

        pointerOpen = true;
        // A descendant's scroll is seen through the window capture listener.
        document.body.dispatchEvent(new window.Event("scroll"));
        resize();
        expect(scheduled).toBe(2);

        dispose();
        scroll();
        resize();
        expect(scheduled).toBe(2);
    });
});
