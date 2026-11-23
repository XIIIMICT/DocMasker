import { describe, expect, it } from "bun:test";

import { highlightTexts } from "@/shared/ui/docx-viewer";

function host(html: string): HTMLElement {
    const root = document.createElement("div");
    root.innerHTML = html;
    highlightTexts(root, ["[PERSON_1]", "[ORGANIZATION_1]"]);
    return root;
}

describe("highlightTexts", () => {
    it("wraps a marker exactly once", () => {
        const root = host("<span>Привет, [PERSON_1]!</span>");
        const marks = root.querySelectorAll("mark.docx-marker");
        expect(marks).toHaveLength(1);
        expect(marks[0]?.textContent).toBe("[PERSON_1]");
        expect(root.textContent).toBe("Привет, [PERSON_1]!");
    });

    it("wraps two markers on one line without duplicating", () => {
        const root = host("Меня зовут [PERSON_1], я в [ORGANIZATION_1]");
        const marks = [...root.querySelectorAll("mark.docx-marker")].map((m) => m.textContent);
        expect(marks).toEqual(["[PERSON_1]", "[ORGANIZATION_1]"]);
        expect(root.textContent).toBe("Меня зовут [PERSON_1], я в [ORGANIZATION_1]");
    });

    it("leaves text without markers untouched", () => {
        const root = host("Просто текст");
        expect(root.querySelectorAll("mark")).toHaveLength(0);
        expect(root.textContent).toBe("Просто текст");
    });

    it("handles a marker split across sibling elements", () => {
        // docx-preview may spread a run over several nodes; a marker that lands
        // whole in one node is still wrapped once.
        const root = host("<span>[PERSON_1]</span><span>, привет</span>");
        const marks = root.querySelectorAll("mark.docx-marker");
        expect(marks).toHaveLength(1);
        expect(marks[0]?.textContent).toBe("[PERSON_1]");
    });

    it("re-applying highlights never nests marks (fast highlight changes)", () => {
        const root = document.createElement("div");
        root.innerHTML = "<span>[PERSON_1] и [ORGANIZATION_1]</span>";
        highlightTexts(root, ["[PERSON_1]"]);
        highlightTexts(root, ["[PERSON_1]", "[ORGANIZATION_1]"]);
        highlightTexts(root, ["[ORGANIZATION_1]"]);
        const marks = root.querySelectorAll("mark.docx-marker");
        expect(marks).toHaveLength(1);
        expect(marks[0]?.textContent).toBe("[ORGANIZATION_1]");
        expect(root.querySelectorAll("mark mark")).toHaveLength(0);
        expect(root.textContent).toBe("[PERSON_1] и [ORGANIZATION_1]");
    });
});
