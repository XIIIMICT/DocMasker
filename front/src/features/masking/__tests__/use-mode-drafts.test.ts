import { afterEach, beforeEach, describe, expect, it } from "bun:test";

import { act, renderHook } from "@testing-library/react";

import { MODE_STORAGE_KEY } from "../types";
import { useModeDrafts } from "../use-mode-drafts";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

beforeEach(() => {
    window.localStorage.clear();
    document.title = "";
});

afterEach(() => {
    window.localStorage.clear();
});

describe("useModeDrafts — two independent drafts", () => {
    it("keeps both drafts when switching modes", () => {
        const { result } = renderHook(() => useModeDrafts());

        act(() => result.current.update({ input: "черновик маскировки" }));
        act(() => result.current.onModeChange("unmask"));
        act(() => result.current.update({ input: "черновик демаскировки" }));

        expect(result.current.state.input).toBe("черновик демаскировки");
        act(() => result.current.onModeChange("mask"));
        expect(result.current.state.input).toBe("черновик маскировки");
    });

    it("an operation started in one mode writes there even after a mode switch", () => {
        const { result } = renderHook(() => useModeDrafts());
        // The captured `update` is the one the in-flight operation closed over.
        const capturedUpdate = result.current.update;
        const capturedReset = result.current.resetResult;

        act(() => result.current.onModeChange("unmask"));
        act(() => capturedUpdate({ result: "[PERSON_1]" }));

        expect(result.current.state.result).toBe(""); // the visible unmask draft
        act(() => result.current.onModeChange("mask"));
        expect(result.current.state.result).toBe("[PERSON_1]");
        act(() => capturedReset());
        expect(result.current.state.result).toBe("");
        expect(result.current.state.input).toBe("");
    });

    it("resetResult clears only the current mode's result", () => {
        const { result } = renderHook(() => useModeDrafts());
        act(() => result.current.update({ input: "ввод", result: "[PERSON_1]" }));
        act(() => result.current.onModeChange("unmask"));
        act(() => result.current.update({ input: "другие данные", result: "[PHONE_1]" }));
        act(() => result.current.resetResult());

        expect(result.current.state.result).toBe("");
        act(() => result.current.onModeChange("mask"));
        expect(result.current.state.result).toBe("[PERSON_1]");
        expect(result.current.state.input).toBe("ввод");
    });
});

describe("useModeDrafts — mode persistence", () => {
    it("writes the selected mode to localStorage and the matching document title", () => {
        const { result } = renderHook(() => useModeDrafts());

        act(() => result.current.onModeChange("unmask"));
        expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("unmask");
        expect(document.title).toBe("ДокМаскер / Демаскировка");

        act(() => result.current.onModeChange("mask"));
        expect(window.localStorage.getItem(MODE_STORAGE_KEY)).toBe("mask");
        expect(document.title).toBe("ДокМаскер / Маскировка");
    });

    it("restores the last mode after hydration with the right title", () => {
        window.localStorage.setItem(MODE_STORAGE_KEY, "unmask");
        const { result } = renderHook(() => useModeDrafts());

        expect(result.current.mode).toBe("unmask");
        expect(result.current.inMaskMode).toBe(false);
        expect(document.title).toBe("ДокМаскер / Демаскировка");
    });

    it("ignores unknown stored modes", () => {
        window.localStorage.setItem(MODE_STORAGE_KEY, "unknown");
        const { result } = renderHook(() => useModeDrafts());

        expect(result.current.mode).toBe("mask");
        expect(document.title).toBe("ДокМаскер / Маскировка");
    });
});
