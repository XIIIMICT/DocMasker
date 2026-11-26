"use client";

import { Button, Dropdown } from "@heroui/react";
import { useMemo } from "react";

import { HoverDropdown } from "@/shared/ui/hover-dropdown";

/** The two app modes, in display order. */
export const MODES = [
    { key: "mask", name: "Маскировка" },
    { key: "unmask", name: "Демаскировка" },
] as const;

export type AppMode = (typeof MODES)[number]["key"];

/**
 * The brand with a mode dropdown, mirroring ReportCollector's project switcher:
 * the app name followed by a ghost button showing the active mode, which opens
 * a menu on hover (see `HoverDropdown`). The switcher lives inside the brand,
 * so the header stays a two-slot layout (brand | theme switcher).
 */
export function Brand({
    mode,
    onModeChange,
}: {
    mode: AppMode;
    onModeChange: (mode: AppMode) => void;
}) {
    // Stable identity: an inline `new Set(...)` changes on every render and
    // loops HeroUI's selection sync ("Maximum update depth exceeded").
    const selectedKeys = useMemo(() => new Set([mode]), [mode]);
    const modeName = MODES.find((item) => item.key === mode)?.name ?? MODES[0].name;

    return (
        <div className="brand" aria-label={`ДокМаскер ${modeName}`}>
            <img
                className="brand__icon"
                src="/ruschem_icon.png"
                alt=""
                onError={(event) => {
                    event.currentTarget.hidden = true;
                }}
            />
            <span className="brand__name">ДокМаскер</span>
            <HoverDropdown
                className="brand-popover"
                trigger={({ ref, onHoverStart, onPress }) => (
                    <Button
                        ref={ref}
                        onHoverStart={onHoverStart}
                        onPress={onPress}
                        className="brand__edition brand__edition--button"
                        variant="ghost"
                        aria-label={`Режим: ${modeName}. Выберите режим`}
                    >
                        {modeName}
                    </Button>
                )}
                content={
                    <Dropdown.Menu
                        aria-label="Режимы работы"
                        selectedKeys={selectedKeys}
                        selectionMode="single"
                        onSelectionChange={(selection) => {
                            const keys = selection === "all" ? [] : [...selection];
                            const next = MODES.find((item) => item.key === keys[0]);
                            if (next) onModeChange(next.key);
                        }}
                    >
                        {MODES.map((item) => (
                            <Dropdown.Item key={item.key} id={item.key} textValue={item.name}>
                                <Dropdown.ItemIndicator />
                                {item.name}
                            </Dropdown.Item>
                        ))}
                    </Dropdown.Menu>
                }
            />
        </div>
    );
}
