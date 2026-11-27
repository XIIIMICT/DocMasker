import { fileURLToPath } from "node:url";

import { expect, test, type Locator } from "@playwright/test";

import { DATA_FILE_INPUT } from "./helpers/selectors";
import { waitForGraceFrames } from "./helpers/timing";

const SAMPLE_DOCX = fileURLToPath(new URL("./fixtures/sample.docx", import.meta.url));
/** Named contract: `docx-viewer.tsx::HIGHLIGHT_DEBOUNCE_MS` is 5 ms; allow for scheduling jitter. */
const HIGHLIGHT_PASS_TIMEOUT_MS = 2000;

type PreviewWatch = { paragraph: Element | null; mutations: number };

/** Read the in-page watcher attached to the rendered preview surface. */
function readWatch(surface: Locator) {
    return surface.evaluate((element) => {
        const world = window as Window & { __dmPreviewWatch?: PreviewWatch };
        const watched = world.__dmPreviewWatch;
        if (!watched) throw new Error("preview watcher was not installed");
        return {
            mutations: watched.mutations,
            // A re-render clears the surface and builds a new <p>; the old
            // node is disconnected. This is the document's render counter.
            sameParagraph:
                watched.paragraph !== null &&
                watched.paragraph === element.querySelector("p") &&
                watched.paragraph.isConnected,
        };
    });
}

test("editing keys does not re-render the restored preview", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /Режим:/ }).hover();
    await page.getByRole("menuitemradio", { name: "Демаскировка", exact: true }).click();
    await page.setInputFiles(DATA_FILE_INPUT, SAMPLE_DOCX);
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    await page
        .getByRole("textbox", { name: "Ключи демаскировки" })
        .fill("Физлица:\n  - Иван Петров\nНомера телефонов:\n  - '+7 900 123-45-67'");
    await page.getByRole("button", { name: "Демаскировать" }).click();

    // The restored preview highlights the two restored values.
    const surface = page.locator(".docx-viewer__surface").last();
    await expect(surface.locator("mark.docx-marker")).toHaveCount(2, { timeout: 30000 });
    // Let the initial debounced highlight pass settle before watching: a
    // mutation seen after the keys edit then proves its own pass ran.
    await waitForGraceFrames(page);
    const before = await surface.locator("p").first().innerText();
    await surface.evaluate((element) => {
        const world = window as Window & { __dmPreviewWatch?: PreviewWatch };
        world.__dmPreviewWatch = {
            paragraph: element.querySelector("p"),
            mutations: 0,
        };
        const observer = new MutationObserver(() => {
            world.__dmPreviewWatch!.mutations += 1;
        });
        observer.observe(element, { childList: true, subtree: true, characterData: true });
    });

    // Editing the keys must NOT re-render the preview.
    await page.getByRole("tab", { name: "Ключи демаскировки" }).click();
    const keys = page.getByRole("textbox", { name: "Ключи демаскировки" });
    await keys.fill(
        "Физлица:\n  - Иван Петров\nНомера телефонов:\n  - '+7 900 123-45-67'\nОрганизации:\n  - ООО «Тест»",
    );
    await expect(keys).toHaveValue(/ООО «Тест»/);

    // Wait for the named debounce contract instead of a magic 500 ms sleep.
    await expect
        .poll(() => readWatch(surface).then((state) => state.mutations), {
            timeout: HIGHLIGHT_PASS_TIMEOUT_MS,
            message: "debounced highlight pass did not run after the keys edit",
        })
        .toBeGreaterThan(0);
    const state = await readWatch(surface);
    expect(state.sameParagraph).toBe(true);
    await expect(surface.locator("mark.docx-marker")).toHaveCount(2);
    expect(await surface.locator("p").first().innerText()).toBe(before);
});
