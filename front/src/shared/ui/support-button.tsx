"use client";

import { Link } from "@heroui/react";
import { HeadsetHelp } from "iconoir-react";

/**
 * The editable part of a message to the developer: the person writes what went
 * wrong; everything above it is fixed letterhead (mirrors ReportCollector).
 */
export const SUPPORT_BODY_INTRO =
    "Здесь вы можете описать обнаруженные проблемы или пожелания по улучшению работы проекта. Если это возможно, прикрепите иллюстрационный материал (скриншоты, схемы) — это поможет лучше вас понять. Обращение будет направлено напрямую разработчику сайта, согласование с техподдержкой не требуется. Если проблема срочная, вы также можете позвонить по телефону.";

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
 * «Связаться с разработчиком» — icon-only in the header until hovered or
 * focused (keyboard users must not face an unlabeled control); the label then
 * expands. It is styled as a tertiary HeroUI `Button` (`button button--tertiary`),
 * but stays an anchor: a `mailto:` needs `Link`, a Button would swallow the
 * activation. HeroUI v3's `Link` has no `variant` prop, so the variant is
 * carried by the classes; `variant` is kept as an explicit, documented prop so
 * the call site reads like the other header controls.
 *
 * The mailbox is read server-side and passed as a prop, so it never has to be
 * baked into the client bundle as a `NEXT_PUBLIC_` value; an empty mailbox
 * hides the control entirely.
 */
export function SupportButton({
    supportMail,
    variant = "tertiary",
}: {
    supportMail: string;
    variant?: "tertiary";
}) {
    if (!supportMail) return null;
    const href = supportMailto(supportMail);
    return (
        <Link
            href={href}
            className={`button button--${variant} support-link`}
            aria-label="Связаться с разработчиком"
        >
            <HeadsetHelp aria-hidden="true" />
            <span className="support-link__label">Связаться с разработчиком</span>
        </Link>
    );
}
