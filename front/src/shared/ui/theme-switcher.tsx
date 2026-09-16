"use client";

import { Tabs, Tooltip } from "@heroui/react";
import { Computer, HalfMoon, SunLight } from "iconoir-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

/** The three theme preferences, with the icons used by the reference project. */
export const THEME_OPTIONS = [
    { id: "system", label: "Системная", Icon: Computer },
    { id: "light", label: "Светлая", Icon: SunLight },
    { id: "dark", label: "Тёмная", Icon: HalfMoon },
] as const satisfies ReadonlyArray<{
    id: "system" | "light" | "dark";
    label: string;
    Icon: typeof Computer;
}>;

export type ThemePreference = (typeof THEME_OPTIONS)[number]["id"];

/** Resolve a `next-themes` value to a known option, defaulting to "system". */
export function resolveTheme(theme: string | undefined): (typeof THEME_OPTIONS)[number] {
    return THEME_OPTIONS.find((option) => option.id === theme) ?? THEME_OPTIONS[0];
}

/**
 * Theme switcher: icon-only `Tabs` (no panels) that switches `next-themes`.
 *
 * Each tab is a single icon; hovering it shows a HeroUI `Tooltip` with the
 * theme name, so the control stays compact in the header. The tabs are
 * controlled through `selectedKey`, since `next-themes` owns the state.
 *
 * The server cannot know the active theme, so "system" is rendered until the
 * client has hydrated (avoids a hydration mismatch on the selected tab).
 */
export function ThemeSwitcher() {
    const { theme, setTheme } = useTheme();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    const selected = mounted ? resolveTheme(theme).id : "system";

    return (
        <Tabs
            className="theme-tabs"
            selectedKey={selected}
            onSelectionChange={(key) => setTheme(String(key))}
        >
            <Tabs.List aria-label="Тема оформления">
                {THEME_OPTIONS.map((option) => (
                    <Tabs.Tab aria-label={option.label} id={option.id} key={option.id}>
                        <Tooltip delay={150}>
                            <Tooltip.Trigger className="theme-tabs__icon">
                                <option.Icon aria-hidden />
                            </Tooltip.Trigger>
                            <Tooltip.Content showArrow placement="bottom">
                                <Tooltip.Arrow />
                                {option.label}
                            </Tooltip.Content>
                        </Tooltip>
                        <Tabs.Indicator />
                    </Tabs.Tab>
                ))}
            </Tabs.List>
        </Tabs>
    );
}
