import { keyEntries } from "@/shared/api/mask";

/**
 * Masking / demasking totals for the result-label tooltip.
 *
 * `byLabel` counts occurrences, `uniqueByLabel` counts distinct values, and
 * `unused` (demasking only) lists key values whose placeholder never appeared
 * in the document. `total` is the sum of `byLabel`.
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

/** Section titles for the tooltip lines (mirrors the backend's `LABEL_TITLES`). */
const LABEL_TITLES: Record<string, string> = {
    person: "Физлица",
    phone: "Номера телефонов",
    email: "Email",
    link: "Ссылки",
    address: "Адреса",
    organization: "Организации",
};

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

/** "5 фрагментов" — the short result-label summary. */
export function totalText(stats: MaskStats): string {
    return `${stats.total} ${plural(stats.total, ["фрагмент", "фрагмента", "фрагментов"])}`;
}

/** "2 уникальных" — the Russian plural of "unique" for a count. */
function uniqueText(count: number): string {
    return plural(count, ["уникальный", "уникальных", "уникальных"]);
}

/**
 * Tooltip body: one line per label, e.g.
 * ```
 * Физлица: 4 (2 уникальных)
 * Организации: 5 (5 уникальных)
 * ```
 * Plus, when some keys were not applied, a trailing block listing them.
 */
export function statsBreakdown(stats: MaskStats): string {
    const lines = Object.entries(stats.byLabel)
        .filter(([, count]) => count > 0)
        .sort(([, a], [, b]) => b - a)
        .map(([label, count]) => {
            const unique = stats.uniqueByLabel[label] ?? count;
            const title = LABEL_TITLES[label] ?? label;
            return `${title}: ${count} (${unique} ${uniqueText(unique)})`;
        });
    if (stats.unused.length > 0) {
        lines.push("", `Не применено ${stats.unused.length}:`);
        for (const value of stats.unused) {
            lines.push(`- ${value};`);
        }
    }
    return lines.join("\n");
}

/** Build masking stats from the detected entities. */
export function countLabels(entities: { label: string; text: string }[]): MaskStats {
    const byLabel: Record<string, number> = {};
    const unique: Record<string, Set<string>> = {};
    for (const entity of entities) {
        byLabel[entity.label] = (byLabel[entity.label] ?? 0) + 1;
        (unique[entity.label] ??= new Set()).add(entity.text);
    }
    return {
        total: entities.length,
        byLabel,
        uniqueByLabel: uniqueCounts(unique),
        unused: [],
    };
}

/**
 * Build demasking stats from the keys and the text they are applied to: the
 * counts mirror masking, and any key whose placeholder is absent from the
 * document is reported as "не применено". Pass `null` for `documentText` when
 * the masked text is unknown (a `.docx` is only known to the backend), which
 * skips the unused scan.
 */
export function countKeysValues(keysYaml: string, documentText: string | null): MaskStats {
    const byLabel: Record<string, number> = {};
    const unique: Record<string, Set<string>> = {};
    const unused: string[] = [];
    for (const [placeholder, value] of keyEntries(keysYaml)) {
        const label = placeholder.slice(1, -1).replace(/_\d+$/, "").toLowerCase();
        byLabel[label] = (byLabel[label] ?? 0) + 1;
        (unique[label] ??= new Set()).add(value);
        if (documentText !== null && !documentText.includes(placeholder)) {
            unused.push(value);
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
