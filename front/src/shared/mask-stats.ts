import type { ReactNode } from "react";
import { createElement } from "react";

import { LABEL_CATALOGUE } from "@/shared/api/labels.generated";
import { parseKeys, type MaskStatsPayload, type ParsedKeysDocument } from "@/shared/api/mask";
import type { ResultManifest } from "@/shared/api/mask-archive";

/**
 * Masking / demasking totals for the result-label tooltip.
 *
 * The headline number is the count of UNIQUE values. `byLabel` keeps the total
 * number of occurrences (for the tooltip's "N с повторами" part),
 * `uniqueByLabel` counts distinct values, and `unused` (demasking only) lists
 * key values whose placeholder never appeared in the document.
 */
export interface MaskStats {
    total: number;
    byLabel: Record<string, number>;
    uniqueByLabel: Record<string, number>;
    unused: string[];
}

/** Russian plural/name forms for the entity labels the backend can return. */
const LABEL_LABELS: Record<string, [one: string, few: string, many: string]> = {
    person: ["физлицо", "физлица", "физлиц"],
    phone: ["телефон", "телефона", "телефонов"],
    email: ["эл. почта", "эл. почты", "эл. почт"],
    link: ["ссылка", "ссылки", "ссылок"],
    address: ["адрес", "адреса", "адресов"],
    organization: ["организация", "организации", "организаций"],
};

/** Section titles for the tooltip lines (canonical, generated from labels.yaml). */
const LABEL_TITLES: Record<string, string> = Object.fromEntries(
    LABEL_CATALOGUE.map((entry) => [entry.id, entry.title]),
);

/** Pick the Russian plural form for `count` (1 фрагмент / 2 фрагмента / 5 фрагментов). */
export function plural(count: number, forms: [string, string, string]): string {
    const mod100 = count % 100;
    const mod10 = count % 10;
    if (mod100 >= 11 && mod100 <= 14) return forms[2];
    if (mod10 === 1) return forms[0];
    if (mod10 >= 2 && mod10 <= 4) return forms[1];
    return forms[2];
}

/** Display name (pluralised) of an entity label. */
export function labelName(label: string, count: number): string {
    const forms = LABEL_LABELS[label];
    if (forms) return plural(count, forms);
    return plural(count, ["фрагмент", "фрагмента", "фрагментов"]);
}

/** Sum of distinct values across categories — the headline fragment count. */
export function uniqueTotal(stats: MaskStats): number {
    return Object.values(stats.uniqueByLabel).reduce((sum, count) => sum + count, 0);
}

/** "5 фрагментов" — the short result-label summary (unique values only). */
export function totalText(stats: MaskStats): string {
    const count = uniqueTotal(stats);
    return `${count} ${plural(count, ["фрагмент", "фрагмента", "фрагментов"])}`;
}

/**
 * Tooltip body with a bold category title, the unique count first and the
 * total occurrences in parentheses, e.g.:
 *
 * **Организации:** 5 (7 с повторами)
 *
 * Unused demasking keys are appended as a trailing block.
 */
export function statsBreakdown(stats: MaskStats): ReactNode[] {
    const nodes: ReactNode[] = [];
    const lines = Object.entries(stats.byLabel)
        .filter(([, count]) => count > 0)
        .sort(([, a], [, b]) => b - a);
    lines.forEach(([label, occurrences], index) => {
        if (index > 0) nodes.push("\n");
        const unique = stats.uniqueByLabel[label] ?? occurrences;
        const title = LABEL_TITLES[label] ?? label;
        nodes.push(createElement("b", { key: `title-${label}` }, `${title}:`));
        nodes.push(` ${unique} (${occurrences} с повторами)`);
    });
    if (stats.unused.length > 0) {
        nodes.push("\n\n", `Не применено ${stats.unused.length}:`);
        for (const value of stats.unused) {
            nodes.push("\n", `- ${value};`);
        }
    }
    return nodes;
}

/** Build masking stats from the backend's aggregate response (no raw values). */
export function statsFromAggregate(stats: MaskStatsPayload): MaskStats {
    return {
        total: stats.total,
        byLabel: stats.occurrences_by_label,
        uniqueByLabel: stats.unique_by_label,
        unused: [],
    };
}

/** Build stats from the versioned file manifest (§7.3 — no header parsing). */
export function statsFromManifest(manifest: ResultManifest): MaskStats {
    const statistics = manifest.statistics;
    return {
        total: statistics.total_occurrences ?? 0,
        byLabel: statistics.occurrences_by_label ?? {},
        uniqueByLabel: statistics.unique_by_label ?? {},
        unused: statistics.unused_keys ?? [],
    };
}

/** Placeholder-shaped tokens in a masked document (`[PERSON_1]`, `[PHONE_2]`, …). */
const PLACEHOLDER_TOKEN = /\[[A-Z][A-Z0-9]*_\d+\]/g;

/**
 * Build demasking stats from the keys and the placeholders they are applied to.
 *
 * Accepts the raw YAML or an already parsed document, so a caller that has just
 * validated the keys does not parse them again on the same edit.
 */
export function countKeysValues(
    keysYaml: string | ParsedKeysDocument,
    documentText: string | null,
    occurrencesByLabel?: Record<string, number>,
): MaskStats {
    return statsFromParsedKeys(
        typeof keysYaml === "string" ? parseKeys(keysYaml) : keysYaml,
        documentText,
        occurrencesByLabel,
    );
}

/**
 * Count occurrences with one scan of the document instead of one `split` per
 * key. Distinct-key counts come from the parsed entries, independently of how
 * many times a placeholder appears. Tokens not present in the keys are ignored,
 * so an unknown placeholder never changes the totals.
 *
 * `uniqueByLabel` comes from the distinct key values. Occurrences are counted
 * from `documentText` when it is known (text mode); for a DOCX the caller passes
 * the counts extracted from the masked file via `occurrencesByLabel`. Any key
 * whose placeholder is absent from the document is reported as "не применено".
 */
export function statsFromParsedKeys(
    parsed: ParsedKeysDocument,
    documentText: string | null,
    occurrencesByLabel?: Record<string, number>,
): MaskStats {
    const unique: Record<string, Set<string>> = {};
    const byLabel: Record<string, number> = { ...occurrencesByLabel };
    const unused: string[] = [];

    const known = new Set(parsed.entries.map(({ placeholder }) => placeholder));
    const occurrences: Record<string, number> = {};
    if (documentText !== null && known.size > 0) {
        for (const match of documentText.matchAll(PLACEHOLDER_TOKEN)) {
            const token = match[0];
            if (known.has(token)) occurrences[token] = (occurrences[token] ?? 0) + 1;
        }
    }
    for (const { placeholder, label, value } of parsed.entries) {
        (unique[label] ??= new Set()).add(value);
        if (documentText !== null) {
            const count = occurrences[placeholder] ?? 0;
            if (count > 0) byLabel[label] = (byLabel[label] ?? 0) + count;
            else unused.push(value);
        } else if (occurrencesByLabel === undefined) {
            byLabel[label] = (byLabel[label] ?? 0) + 1;
        }
    }
    return {
        total: Object.values(byLabel).reduce((sum, count) => sum + count, 0),
        byLabel,
        uniqueByLabel: uniqueCounts(unique),
        unused,
    };
}

function uniqueCounts(unique: Record<string, Set<string>>): Record<string, number> {
    const result: Record<string, number> = {};
    for (const [label, values] of Object.entries(unique)) {
        result[label] = values.size;
    }
    return result;
}
