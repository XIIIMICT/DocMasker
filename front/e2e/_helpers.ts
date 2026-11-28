import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

export const MASK_DATA = "Данные для маскировки";
export const DEMASK_DATA = "Данные для демаскировки";

/** A minimal .docx fixture (one paragraph with a name and a phone). */
export const SAMPLE_DOCX = fileURLToPath(new URL("./fixtures/sample.docx", import.meta.url));
/** The same fixture already masked: `Контакты: [PERSON_1], тел [PHONE_1].` */
export const SAMPLE_MASKED_DOCX = fileURLToPath(
    new URL("./fixtures/sample-masked.docx", import.meta.url),
);

/** Switch the app mode through the brand dropdown (opens on hover). */
export async function switchMode(page: Page, name: string): Promise<void> {
    await page.getByRole("button", { name: /Режим:/ }).hover();
    await page.getByRole("menuitemradio", { name, exact: true }).click();
}
