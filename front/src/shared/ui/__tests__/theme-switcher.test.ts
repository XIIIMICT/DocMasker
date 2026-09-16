import { describe, expect, it } from "bun:test";

import { resolveTheme, THEME_OPTIONS } from "@/shared/ui/theme-switcher";

describe("resolveTheme", () => {
    it("maps each known preference to its option", () => {
        expect(resolveTheme("system").id).toBe("system");
        expect(resolveTheme("light").id).toBe("light");
        expect(resolveTheme("dark").id).toBe("dark");
    });

    it("falls back to the system option for an unknown or missing value", () => {
        // `undefined` happens on the first render, before next-themes hydrates.
        expect(resolveTheme(undefined).id).toBe("system");
        expect(resolveTheme("sepia").id).toBe("system");
    });
});

describe("THEME_OPTIONS", () => {
    it("offers the three documented labels in order", () => {
        expect(THEME_OPTIONS.map((option) => option.label)).toEqual([
            "Системная",
            "Светлая",
            "Тёмная",
        ]);
    });
});
