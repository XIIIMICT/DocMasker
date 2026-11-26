import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { disabledProps } from "@/shared/ui/pane-types";

const UI_DIR = fileURLToPath(new URL("..", import.meta.url));

function source(relativeToUi: string): string {
    return readFileSync(join(UI_DIR, relativeToUi), "utf8");
}

describe("pane module boundaries", () => {
    it("keeps the hydration-safe disabled props stable", () => {
        const on = disabledProps(true);
        expect(on["aria-disabled"]).toBe(true);
        expect(on.className).toBe("action-disabled");

        const off = disabledProps(false);
        expect(off["aria-disabled"]).toBe(false);
        expect(off.className).toBeUndefined();
    });

    it("keeps the heavy document libraries out of the eager pane module graph", () => {
        // lazy-zip.test.ts covers the JSZip loader and the build checks that
        // chunks resolve; neither detects a pane importing a browser-only
        // library eagerly. This one guard covers the import graph; the
        // accessible-disabled behavior lives in action-boundaries.test.tsx.
        for (const file of [
            "pane-types.ts",
            "doc-preview-shell.tsx",
            "input-panes.tsx",
            "result-panes.tsx",
        ]) {
            const text = source(file);
            expect(text).not.toMatch(/from\s+["'](?:docx-preview|jszip)["']/);
            expect(text).not.toMatch(/import\s*\(\s*["'](?:docx-preview|jszip)["']\s*\)/);
        }
        expect(source("docx-viewer.tsx")).toMatch(/await import\(\s*["']docx-preview["']\s*\)/);
    });
});
