import { afterEach, describe, expect, it, mock } from "bun:test";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { Brand } from "@/shared/ui/brand";

declare global {
    var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.HTMLButtonElement = window.HTMLButtonElement;
globalThis.HTMLAnchorElement = window.HTMLAnchorElement;
globalThis.HTMLInputElement = window.HTMLInputElement;
globalThis.HTMLTextAreaElement = window.HTMLTextAreaElement;
globalThis.HTMLSelectElement = window.HTMLSelectElement;
globalThis.CSS = window.CSS;

afterEach(() => cleanup());

async function openMenu(): Promise<HTMLElement> {
    const trigger = screen.getByRole("button", { name: /Режим:/ });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });
    fireEvent.keyUp(trigger, { key: "Enter" });
    await screen.findByRole("menuitemradio", { name: "Демаскировка" });
    return trigger;
}

function press(element: HTMLElement) {
    fireEvent.keyDown(element, { key: "Enter" });
    fireEvent.keyUp(element, { key: "Enter" });
}

describe("brand mode menu accessibility", () => {
    it("exposes a named brand group and a named mode menu", async () => {
        render(<Brand mode="mask" onModeChange={() => undefined} />);
        expect(screen.getByRole("group", { name: "ДокМаскер Маскировка" })).toBeTruthy();
        expect(
            screen.getByRole("button", { name: "Режим: Маскировка. Выберите режим" }),
        ).toBeTruthy();
        await openMenu(); // already proves the "Демаскировка" radio is present
        expect(screen.getByRole("menu")).toBeTruthy();
        expect(screen.getByRole("menuitemradio", { name: "Маскировка" })).toBeTruthy();
    });

    it("switches mode by keyboard through the menu", async () => {
        const onModeChange = mock(() => undefined);
        render(<Brand mode="mask" onModeChange={onModeChange} />);
        await openMenu();
        press(screen.getByRole("menuitemradio", { name: "Демаскировка" }));
        expect(onModeChange).toHaveBeenCalledWith("unmask");
    });

    it("Escape closes the menu and leaves focus on the trigger", async () => {
        render(<Brand mode="mask" onModeChange={() => undefined} />);
        const trigger = await openMenu();
        const item = screen.getByRole("menuitemradio", { name: "Демаскировка" });
        item.focus();
        fireEvent.keyDown(window, { key: "Escape" });
        await act(async () => {
            await Promise.resolve();
        });
        expect(screen.queryByRole("menuitemradio")).toBeNull();
        expect(document.activeElement === trigger).toBe(true);
    });
});
