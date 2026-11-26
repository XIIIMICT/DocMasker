import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import type { DocIcon, DocPreviewShell } from "@/shared/ui/doc-preview-shell";
import type { DemaskInputPane, MaskInputPane } from "@/shared/ui/input-panes";
import { disabledProps, type DragHandlers } from "@/shared/ui/pane-types";
import type { MaskResultPane, ResultBody, UnmaskResultPane } from "@/shared/ui/result-panes";

const UI_DIR = fileURLToPath(new URL("..", import.meta.url));

function source(relativeToUi: string): string {
    return readFileSync(join(UI_DIR, relativeToUi), "utf8");
}

/** Type-only check: the public name must still be an exported component. */
type ComponentShape = (props: never) => unknown;
function isComponent<_T extends ComponentShape>(): true {
    return true;
}

describe("pane module boundaries", () => {
    it("exposes the same public components from their owning modules", () => {
        expect(isComponent<typeof DocIcon>()).toBe(true);
        expect(isComponent<typeof DocPreviewShell>()).toBe(true);
        expect(isComponent<typeof MaskInputPane>()).toBe(true);
        expect(isComponent<typeof DemaskInputPane>()).toBe(true);
        expect(isComponent<typeof MaskResultPane>()).toBe(true);
        expect(isComponent<typeof ResultBody>()).toBe(true);
        expect(isComponent<typeof UnmaskResultPane>()).toBe(true);
    });

    it("keeps the hydration-safe disabled workaround intact", () => {
        const helper = source("pane-types.ts");
        expect(helper).toContain(
            "trips React's hydration check — `aria-disabled` + a class is stable.",
        );
        const on = disabledProps(true);
        expect(on["aria-disabled"]).toBe(true);
        expect(on.className).toBe("action-disabled");
        const off = disabledProps(false);
        expect(off["aria-disabled"]).toBe(false);
        expect(off.className).toBeUndefined();
    });

    it("exposes the drag/drop contract used by both input panes and the workflow", () => {
        const drag: DragHandlers = {
            dragging: false,
            onDragEnter: () => {},
            onDragOver: () => {},
            onDragLeave: () => {},
            onDrop: () => {},
        };
        expect(Object.keys(drag)).toEqual([
            "dragging",
            "onDragEnter",
            "onDragOver",
            "onDragLeave",
            "onDrop",
        ]);
    });

    it("never makes the document libraries an eager dependency", () => {
        for (const file of [
            "pane-types.ts",
            "doc-preview-shell.tsx",
            "input-panes.tsx",
            "result-panes.tsx",
        ]) {
            const text = source(file);
            expect(text).not.toMatch(/from\s+["']docx-preview["']/);
            expect(text).not.toMatch(/from\s+["']jszip["']/);
            expect(text).not.toMatch(/import\s*\(\s*["'](?:docx-preview|jszip)["']\s*\)/);
        }
        // The only import of docx-preview stays lazy, inside the viewer itself.
        expect(source("docx-viewer.tsx")).toContain('await import("docx-preview")');
    });

    it("leaves no imports of the retired panes module", () => {
        expect(source("../../app/masker-page.tsx")).not.toContain("@/shared/ui/panes");
        expect(source("../../features/masking/use-file-input.ts")).not.toContain(
            "@/shared/ui/panes",
        );
    });
});
