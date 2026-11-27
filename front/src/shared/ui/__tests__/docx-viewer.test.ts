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

/** Render `html`, apply exactly `needles`, and read back the mark contents. */
function marked(html: string, needles: string[]): { root: HTMLElement; texts: string[] } {
    const root = document.createElement("div");
    root.innerHTML = html;
    highlightTexts(root, needles);
    const texts = [...root.querySelectorAll("mark.docx-marker")].map(
        (mark) => mark.textContent ?? "",
    );
    return { root, texts };
}

describe("highlightTexts correctness contract", () => {
    // Product rule: an occurrence at the same start position belongs to the
    // LONGEST matching needle, regardless of keys order. This mirrors the
    // backend's longest-first unmask alternation (mapping.py::compile_placeholders).
    // Equal-length needles cannot both match at one position, so keys order
    // only decides the (irrelevant) order of equal-length alternatives.
    it("overlapping needles: the longest match wins in either keys order", () => {
        expect(marked("abcdef", ["abc", "abcdef"]).texts).toEqual(["abcdef"]);
        expect(marked("abcdef", ["abcdef", "abc"]).texts).toEqual(["abcdef"]);
        // A needle starting inside the longer match is consumed by it.
        expect(marked("abcdef", ["cdef", "abcdef"]).texts).toEqual(["abcdef"]);
    });

    it("overlapping needles: a shorter needle still matches where the longer cannot", () => {
        // Earliest start wins; at that start the longest candidate wins.
        expect(marked("xabcd", ["bcd", "a"]).texts).toEqual(["a", "bcd"]);
        expect(marked("xabcd", ["a", "bcd"]).texts).toEqual(["a", "bcd"]);
    });

    it("prefix collisions: a shorter restored value never shadows a longer one", () => {
        // Keys may hold both «Иван» and «Иван Петров»; the full value must win.
        expect(marked("Иван Петров и Иван", ["Иван", "Иван Петров"]).texts).toEqual([
            "Иван Петров",
            "Иван",
        ]);
        expect(marked("Иван Петров и Иван", ["Иван Петров", "Иван"]).texts).toEqual([
            "Иван Петров",
            "Иван",
        ]);
        // Numbered markers do not share a literal prefix because of the `]`.
        expect(marked("[PERSON_10]", ["[PERSON_1]", "[PERSON_10]"]).texts).toEqual(["[PERSON_10]"]);
    });

    it("repeated occurrences are all marked, without duplicating text", () => {
        const { root, texts } = marked("[X] и [X] и [X]", ["[X]"]);
        expect(texts).toEqual(["[X]", "[X]", "[X]"]);
        expect(root.textContent).toBe("[X] и [X] и [X]");
    });

    it("empty needles are ignored; an empty list removes previous marks", () => {
        expect(marked("нет совпадений", [""]).texts).toEqual([]);
        expect(marked("[X]", ["", "[X]"]).texts).toEqual(["[X]"]);
        const root = document.createElement("div");
        root.innerHTML = "<p>[X]</p>";
        highlightTexts(root, ["[X]"]);
        expect(root.querySelectorAll("mark.docx-marker")).toHaveLength(1);
        highlightTexts(root, []);
        expect(root.querySelectorAll("mark.docx-marker")).toHaveLength(0);
        expect(root.textContent).toBe("[X]");
    });

    it("needles are literal strings, not regex patterns", () => {
        expect(marked("a+b axb a*b", ["a+b", "a*b"]).texts).toEqual(["a+b", "a*b"]);
    });

    it("Unicode: non-BMP characters and exact combining sequences", () => {
        expect(marked("Привет 😀 мир", ["😀"]).texts).toEqual(["😀"]);
        const decomposed = marked("cafe\u0301 и café", ["e\u0301"]);
        expect(decomposed.texts).toEqual(["e\u0301"]);
        expect(decomposed.root.textContent).toBe("cafe\u0301 и café");
        expect(marked("cafe\u0301 и café", ["é"]).texts).toEqual(["é"]);
    });

    it("a needle split across neighbouring text nodes is highlighted once per node slice", () => {
        // docx-preview can spread one marker over several w:t runs.
        const { root, texts } = marked(
            "<p><span>[PER</span><span>SON_1]</span> и <span>[PERSON_2]</span></p>",
            ["[PERSON_1]", "[PERSON_2]"],
        );
        expect(texts).toEqual(["[PER", "SON_1]", "[PERSON_2]"]);
        expect(root.textContent).toBe("[PERSON_1] и [PERSON_2]");
    });

    it("a needle spanning three neighbouring nodes is mapped back to each slice", () => {
        const { root, texts } = marked("<p><span>[PE</span><span>RSON</span><span>_1]</span></p>", [
            "[PERSON_1]",
        ]);
        expect(texts).toEqual(["[PE", "RSON", "_1]"]);
        expect(root.textContent).toBe("[PERSON_1]");
    });

    it("matching never crosses a paragraph boundary", () => {
        expect(marked("<p>abc</p><p>def</p>", ["cd"]).texts).toEqual([]);
    });

    it("reapplying after a split-needle pass stays idempotent", () => {
        const root = document.createElement("div");
        root.innerHTML = "<p><span>[PER</span><span>SON_1]</span></p>";
        highlightTexts(root, ["[PERSON_1]"]);
        highlightTexts(root, ["[PERSON_1]"]);
        expect(
            [...root.querySelectorAll("mark.docx-marker")].map((mark) => mark.textContent),
        ).toEqual(["[PER", "SON_1]"]);
        expect(root.querySelectorAll("mark mark")).toHaveLength(0);
        expect(root.querySelectorAll("mark.docx-marker")).toHaveLength(2);
        highlightTexts(root, ["[PERSON_2]"]);
        expect(root.querySelectorAll("mark.docx-marker")).toHaveLength(0);
        expect(root.textContent).toBe("[PERSON_1]");
    });
});
