import { describe, expect, it } from "bun:test";

import { relativeScrollTop } from "@/shared/hooks/use-synced-scroll";

function makeElement(scrollHeight: number, clientHeight: number, scrollTop: number): HTMLElement {
    return { scrollHeight, clientHeight, scrollTop } as HTMLElement;
}

describe("relativeScrollTop", () => {
    it("maps the scroll ratio onto a longer target", () => {
        // Halfway down a 200px range -> halfway down a 400px range.
        const source = makeElement(300, 100, 100);
        const target = makeElement(500, 100, 0);
        expect(relativeScrollTop(source, target)).toBe(200);
    });

    it("is 0 at the top of the source", () => {
        const source = makeElement(300, 100, 0);
        const target = makeElement(500, 100, 50);
        expect(relativeScrollTop(source, target)).toBe(0);
    });

    it("returns null when a pane cannot scroll", () => {
        const source = makeElement(300, 100, 50);
        const noScroll = makeElement(100, 100, 0);
        expect(relativeScrollTop(source, noScroll)).toBeNull();
        expect(relativeScrollTop(noScroll, source)).toBeNull();
    });
});
