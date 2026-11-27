import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../globals.css", import.meta.url), "utf8");
const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selector: string; body: string };

/** The patterns below always have the group; fail loudly if one ever drifts. */
function capture(match: RegExpMatchArray, group: 1 | 2): string {
    const value = match[group];
    if (value === undefined) throw new Error("missing regex capture");
    return value;
}

/** Flat list of innermost CSS rules; @media wrappers have nested braces and are skipped. */
function rules(text: string): Rule[] {
    return [...text.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
        selector: capture(match, 1).trim().replace(/\s+/g, " "),
        body: capture(match, 2),
    }));
}

function reducedMotionBlock(): string {
    const marker = "@media (prefers-reduced-motion: reduce)";
    const start = withoutComments.indexOf(marker);
    if (start < 0) return "";
    const open = withoutComments.indexOf("{", start + marker.length);
    let depth = 0;
    for (let index = open; index < withoutComments.length; index += 1) {
        const char = withoutComments[index];
        if (char === "{") depth += 1;
        if (char === "}") {
            depth -= 1;
            if (depth === 0) return withoutComments.slice(open + 1, index);
        }
    }
    return "";
}

describe("globals.css reduced-motion policy", () => {
    it("covers every app-owned animated selector", () => {
        const animated = rules(withoutComments)
            .filter((rule) =>
                /(?:^|;)\s*(?:transition|animation|scroll-behavior)\s*:/.test(rule.body),
            )
            .flatMap((rule) => rule.selector.split(",").map((selector) => selector.trim()))
            .filter((selector) => selector.length > 0 && !selector.startsWith("@"))
            .sort();

        expect(animated).toEqual([".support-link", ".support-link__label"]);
        // Theme changes are instant by contract: next-themes injects
        // `transition:none!important`, so a stylesheet transition here is dead.
        expect(withoutComments).not.toMatch(/html\s*\{[^}]*\btransition\s*:/);
        expect(withoutComments).not.toMatch(/--app-motion-theme\s*:/);
        // Maintainer decision 2026-11-28: no transform-based shortcut remains.
        expect(withoutComments).not.toMatch(/translateX\(/);
    });

    it("routes every transition duration through an app motion token", () => {
        const transitions = [...withoutComments.matchAll(/transition\s*:\s*([^;]+);/g)].map(
            (match) => capture(match, 1),
        );

        expect(transitions).toHaveLength(2);
        for (const transition of transitions) {
            expect(transition).toMatch(/var\(--app-motion-[\w-]+\)/);
            expect(transition).not.toMatch(/\d+(?:\.\d+)?m?s\b/);
        }
    });

    it("restores the original support-pill expansion timing", () => {
        const transitions = [...withoutComments.matchAll(/transition\s*:\s*([^;]+);/g)].map(
            (match) => capture(match, 1),
        );
        const [linkTransition, labelTransition] = transitions;
        if (!linkTransition || !labelTransition) throw new Error("missing transition declarations");

        // Restored by maintainer decision 2026-11-28 after the user reported
        // that the audited snap felt too fast. The layout cost is accepted.
        expect(linkTransition).toContain(
            "gap var(--app-motion-gap) cubic-bezier(0.22, 1, 0.36, 1)",
        );
        expect(linkTransition).toContain("background-color var(--app-motion-fast) ease");
        expect(labelTransition).toContain(
            "max-width var(--app-motion-expand) cubic-bezier(0.22, 1, 0.36, 1)",
        );
        expect(labelTransition).toContain("opacity var(--app-motion-fast) ease");

        // First declarations are the normal-motion tokens; the reduce block
        // overrides them with 0ms and is asserted separately below.
        const values = new Map<string, string>();
        for (const match of withoutComments.matchAll(/(--app-motion-[\w-]+)\s*:\s*([^;]+);/g)) {
            const name = capture(match, 1);
            if (!values.has(name)) values.set(name, capture(match, 2).trim());
        }
        expect(values.get("--app-motion-fast")).toBe("140ms");
        expect(values.get("--app-motion-gap")).toBe("180ms");
        expect(values.get("--app-motion-expand")).toBe("220ms");

        const labelRule = rules(withoutComments).find(
            (rule) => rule.selector === ".support-link__label",
        );
        const hoverRule = rules(withoutComments).find(
            (rule) =>
                rule.selector ===
                ".support-link:hover .support-link__label, .support-link:focus-visible .support-link__label",
        );
        const compactRule = rules(withoutComments).find(
            (rule) =>
                rule.selector ===
                ".support-link--compact:hover .support-link__label, .support-link--compact:focus-visible .support-link__label",
        );
        for (const body of [labelRule?.body, hoverRule?.body, compactRule?.body]) {
            expect(body).not.toMatch(/transform\s*:/);
        }
        expect(hoverRule?.body).toMatch(/max-width:\s*20rem/);
        expect(hoverRule?.body).toMatch(/opacity:\s*1\b/);
        expect(compactRule?.body).toMatch(/max-width:\s*0\b/);
        expect(compactRule?.body).toMatch(/opacity:\s*0\b/);
    });

    it("zeroes every token referenced by an animated selector inside the reduce block", () => {
        const block = reducedMotionBlock();
        expect(block).not.toBe("");

        const transitions = [...withoutComments.matchAll(/transition\s*:\s*([^;]+);/g)].map(
            (match) => capture(match, 1),
        );
        const referenced = [
            ...new Set(
                [...transitions.join(" ").matchAll(/var\((--app-motion-[\w-]+)\)/g)].map((match) =>
                    capture(match, 1),
                ),
            ),
        ].sort();
        const reducedTokens = new Map(
            [...block.matchAll(/(--app-motion-[\w-]+)\s*:\s*([^;]+);/g)].map((match) => [
                capture(match, 1),
                capture(match, 2).trim(),
            ]),
        );

        expect(referenced).toEqual([...reducedTokens.keys()].sort());
        for (const token of referenced) {
            expect(reducedTokens.get(token)).toBe("0ms");
        }
    });

    it("does not override HeroUI motion policy or hide the end states", () => {
        const block = reducedMotionBlock();

        expect(rules(block).map((rule) => rule.selector)).toEqual([":root"]);
        expect(block).not.toMatch(/!important|will-change|display\s*:|visibility\s*:|opacity\s*:/);
        expect(css).not.toMatch(/will-change/);

        const hoverRule = rules(withoutComments).find(
            (rule) => rule.selector === ".support-link:hover, .support-link:focus-visible",
        );
        expect(hoverRule?.body).toMatch(/gap:\s*0\.45rem/);

        const labelRule = rules(withoutComments).find(
            (rule) =>
                rule.selector ===
                ".support-link:hover .support-link__label, .support-link:focus-visible .support-link__label",
        );
        expect(labelRule?.body).toMatch(/max-width:\s*20rem/);
        expect(labelRule?.body).toMatch(/opacity:\s*1\b/);
    });
});

describe("globals.css breakpoint conventions", () => {
    it("keeps the documented HeroUI sm/md pin and app-owned layout widths", () => {
        expect(css).toMatch(/--breakpoint-sm:\s*0rem;/);
        expect(css).toMatch(/--breakpoint-md:\s*0rem;/);

        const mediaQueries = [...css.matchAll(/@media\s*([^{}]+)\s*\{/g)].map((match) =>
            capture(match, 1).trim(),
        );
        expect(mediaQueries).toContain("(width < 64rem)");
        expect(mediaQueries).toContain("(width < 40rem)");
        expect(mediaQueries).toContain("(prefers-reduced-motion: reduce)");
    });

    it("does not rely on Tailwind sm:/md: utilities in app stylesheets", () => {
        expect(withoutComments).not.toMatch(/(?:^|[\s"])(?:sm|md):/);
    });
});
