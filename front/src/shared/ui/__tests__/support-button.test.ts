import { describe, expect, test } from "bun:test";

import { SUPPORT_BODY_INTRO, supportMailto } from "../support-button";

/**
 * «Связаться с разработчиком» opens a `mailto:` that a mail client must be
 * able to parse: the recipient and subject are plain, the body is the fixed
 * letterhead the user is asked to extend (mirrors ReportCollector).
 */
describe("supportMailto", () => {
    const decode = (value: string) => decodeURIComponent(value.replace(/\+/g, " "));

    test("addresses the configured mailbox", () => {
        expect(supportMailto("support@example.ru")).toStartWith("mailto:support@example.ru?");
    });

    test("names the project in the subject", () => {
        const url = supportMailto("support@example.ru");
        const subject = new URLSearchParams(url.split("?")[1]).get("subject") ?? "";
        expect(subject).toBe("Обращение по ДокМаскер");
    });

    test("carries the letterhead in the body and leaves room to type", () => {
        const url = supportMailto("support@example.ru");
        const body = new URLSearchParams(url.split("?")[1]).get("body") ?? "";
        expect(body).toStartWith(SUPPORT_BODY_INTRO);
        expect(body.endsWith("\n\n\n")).toBe(true);
    });

    test("escapes characters that mail clients mangle", () => {
        const url = supportMailto("support@example.ru", "ДокМаскер (тест)");
        expect(url).not.toContain("(");
        expect(decode(url.split("subject=")[1]!.split("&")[0]!)).toContain("(тест)");
    });
});
