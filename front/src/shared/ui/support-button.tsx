"use client";

import { Link, Tooltip } from "@heroui/react";
import { HeadsetHelp } from "iconoir-react";
import { useSyncExternalStore } from "react";

/**
 * The editable part of a message to the developer: the person writes what went
 * wrong; everything above it is fixed letterhead (mirrors ReportCollector).
 */
export const SUPPORT_BODY_INTRO =
    "Здесь вы можете описать обнаруженные проблемы или пожелания по улучшению работы проекта. Если это возможно, прикрепите иллюстрационный материал (скриншоты, схемы) — это поможет лучше вас понять. Обращение будет направлено напрямую разработчику сайта, согласование с техподдержкой не требуется. Если проблема срочная, вы также можете позвонить по телефону.";

/** Breakpoint below which the header name is hidden and the label becomes a Tooltip. */
export const COMPACT_HEADER_QUERY = "(width < 40rem)";

/**
 * Build the `mailto:` a person opens to write to the developer.
 *
 * `encodeURIComponent` leaves `!'()*` alone: legal in a mailto query, but
 * some mail clients mangle them, so they are escaped too. `@` stays literal
 * in the address.
 */
export function supportMailto(to: string, projectName = "ДокМаскер"): string {
    const encode = (value: string) =>
        encodeURIComponent(value).replace(
            /[!'()*]/g,
            (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`,
        );
    const subject = `Обращение по ${projectName}`;
    const body = `${SUPPORT_BODY_INTRO}\n\n\n`;
    return `mailto:${to}?subject=${encode(subject)}&body=${encode(body)}`;
}

/**
 * Follow a CSS media query without a hydration mismatch: the server snapshot
 * is always `false` (wide), the client re-renders immediately after mount.
 */
function useMediaQuery(query: string): boolean {
    return useSyncExternalStore(
        (onChange) => {
            const list = window.matchMedia(query);
            list.addEventListener("change", onChange);
            return () => list.removeEventListener("change", onChange);
        },
        () => window.matchMedia(query).matches,
        () => false,
    );
}

const LABEL = "Связаться с разработчиком";

/**
 * «Связаться с разработчиком» — a tertiary, icon-only `mailto:` link:
 *
 * - wide screens: the label expands on hover/focus (keyboard users must not
 *   face an unlabeled control);
 * - narrow screens (`COMPACT_HEADER_QUERY`): the label never expands and a
 *   HeroUI `Tooltip` carries the name instead, so the header stays compact.
 *
 * A `mailto:` needs an anchor, not a Button — the button component would
 * swallow the activation. HeroUI v3's `Link` has no `variant` prop, so the
 * tertiary variant is carried by `button button--tertiary` classes; the
 * `variant` prop is kept as an explicit, documented call-site hint.
 * The mailbox is read server-side and passed in, never baked into the bundle.
 */
export function SupportButton({
    supportMail,
    variant = "tertiary",
}: {
    supportMail: string;
    variant?: "tertiary";
}) {
    if (!supportMail) return null;
    const compact = useMediaQuery(COMPACT_HEADER_QUERY);
    const href = supportMailto(supportMail);
    const link = (
        <Link
            href={href}
            className={`button button--${variant} support-link${compact ? " support-link--compact" : ""}`}
            aria-label={LABEL}
        >
            <HeadsetHelp aria-hidden="true" />
            <span className="support-link__label">{LABEL}</span>
        </Link>
    );
    if (!compact) return link;
    // The trigger must BE the anchor: react-aria attaches hover/focus
    // handlers to the trigger element, so a wrapping div-trigger never sees
    // a pointer over the mailto: link. The generic pins the render props to
    // an <a> (HeroUI v3 DOMRenderProps).
    return (
        <Tooltip delay={150}>
            <Tooltip.Trigger<"a">
                render={(props) => (
                    <a
                        {...props}
                        href={href}
                        className={`button button--${variant} support-link support-link--compact`}
                        aria-label={LABEL}
                    >
                        <HeadsetHelp aria-hidden="true" />
                        <span className="support-link__label">{LABEL}</span>
                    </a>
                )}
            />
            <Tooltip.Content showArrow placement="bottom">
                <Tooltip.Arrow />
                {LABEL}
            </Tooltip.Content>
        </Tooltip>
    );
}
