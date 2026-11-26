/**
 * The single client-side boundary for the SUPPORTED demasking-keys YAML subset.
 *
 * `back/src/masking/mapping.py::validate_keys_yaml` stays authoritative: this
 * module deliberately accepts only `Раздел:` + `- value` lines (plus the legacy
 * `Профиль` marker) and mirrors the backend's Russian errors. It is pure and
 * independently testable; callers may memoize its result by the keys string.
 */
import { KEYS_SECTION_TITLES, KEYS_TITLE_LABELS } from "@/shared/api/labels.generated";

/** A recognised section, with its values kept in document order. */
export interface KeysSection {
    title: string;
    label: string;
    values: string[];
}

/** One `(placeholder, original value)` pair produced by a section entry. */
export interface KeysEntry {
    placeholder: string;
    label: string;
    title: string;
    value: string;
}

/** The lenient view every caller needs, even when validation fails. */
export interface ParsedKeysDocument {
    sections: KeysSection[];
    entries: KeysEntry[];
}

/**
 * Either a validated document, or a Russian validation error.
 *
 * Both branches carry the lenient view: highlighting historically keeps working
 * on malformed keys (unknown sections are skipped, empty values ignored), so
 * extraction must not depend on the validity verdict.
 */
export type KeysParseResult =
    | ({ valid: true } & ParsedKeysDocument)
    | ({ valid: false; error: string } & ParsedKeysDocument);

/** The single metadata key of a keys file (`Профиль`); not a section. */
const KEYS_ENVELOPE_KEYS: readonly string[] = ["Профиль"];

/** A top-level section header line (`Физлица:`), used to spot duplicate sections. */
const SECTION_LINE = /^([^\s#][^:]*):\s*$/;

/**
 * Parse and validate a keys document in one pass over the small YAML subset.
 *
 * Validation order matches the backend: empty text, duplicated section headers
 * (from raw lines, because YAML keeps only the last repeated key), then the
 * parsed shape. Unknown/duplicate/empty values are rejected exactly as there.
 */
export function parseKeys(text: string): KeysParseResult {
    if (text.trim().length === 0) return failure("Файл ключей пуст.");

    const seenSections = new Set<string>();
    for (const line of text.split(/\r?\n/)) {
        const match = line.match(SECTION_LINE);
        if (match === null) continue;
        const title = match[1].trim();
        if (KEYS_ENVELOPE_KEYS.includes(title)) continue;
        if (seenSections.has(title)) {
            return failure(`Раздел «${title}» встречается в файле больше одного раза.`);
        }
        seenSections.add(title);
    }

    let parsed: Record<string, unknown> | unknown[];
    try {
        parsed = parseSimpleYaml(text);
    } catch {
        return failure("Файл ключей не является корректным YAML.");
    }
    if (parsed === null || parsed === undefined) return failure("Файл ключей пуст.");
    if (typeof parsed !== "object" || Array.isArray(parsed)) {
        return failure("Файл ключей должен содержать словарь разделов.");
    }

    const record = parsed as Record<string, unknown>;
    const view = lenientView(record);
    const hasEnvelope = KEYS_ENVELOPE_KEYS.some((key) => key in record);
    const entries = Object.entries(record).filter(([title]) => !KEYS_ENVELOPE_KEYS.includes(title));
    if (entries.length === 0) {
        return failure(
            hasEnvelope
                ? "Файл ключей содержит метаданные, но не содержит ни одного раздела."
                : "Файл ключей не содержит ни одного раздела.",
            view,
        );
    }
    for (const [title, values] of entries) {
        if (!(KEYS_SECTION_TITLES as readonly string[]).includes(title)) {
            const known = KEYS_SECTION_TITLES.map((name) => `«${name}»`).join(", ");
            return failure(`Неизвестный раздел ключей: «${title}». Ожидаются: ${known}.`, view);
        }
        if (!Array.isArray(values)) {
            return failure(`Раздел «${title}» должен быть списком значений.`, view);
        }
        if (!values.every((value) => typeof value === "string" && value.trim().length > 0)) {
            return failure(`Раздел «${title}» должен содержать непустые строки.`, view);
        }
        if (new Set(values).size !== values.length) {
            return failure(`Раздел «${title}» содержит дубликаты значений.`, view);
        }
    }
    return { valid: true, ...view };
}

function failure(
    error: string,
    view: ParsedKeysDocument = { sections: [], entries: [] },
): KeysParseResult {
    return { valid: false, error, ...view };
}

/** Build the lenient extraction view without applying validation rules. */
function lenientView(record: Record<string, unknown>): ParsedKeysDocument {
    const sections: KeysSection[] = [];
    const entries: KeysEntry[] = [];
    for (const [title, values] of Object.entries(record)) {
        const label = KEYS_TITLE_LABELS[title];
        if (label === undefined || !Array.isArray(values)) continue;
        const strings: string[] = [];
        values.forEach((value, index) => {
            if (typeof value !== "string") return;
            strings.push(value);
            // Numbering uses the original array position: an empty item before
            // a value shifts that value's placeholder (legacy extraction rule).
            if (value.length === 0) return;
            entries.push({
                placeholder: `[${label.toUpperCase()}_${index + 1}]`,
                label,
                title,
                value,
            });
        });
        sections.push({ title, label, values: strings });
    }
    return { sections, entries };
}

/**
 * Minimal YAML reader for the keys subset: `Раздел:` followed by `- value`
 * lines. Deliberately strict and dependency-free — anything the backend's
 * YAML 1.2 parser could accept but this rejects is rare for keys files, and the
 * backend re-validates anyway (the client check is only a fast gate).
 */
export function parseSimpleYaml(text: string): Record<string, unknown> | unknown[] {
    const result: Record<string, unknown> = {};
    let currentTitle: string | null = null;
    let isTopLevelList = true;
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.replace(/\s+$/, "");
        if (line.trim().length === 0 || line.trimStart().startsWith("#")) continue;
        // A section header followed by a list: `Раздел:`
        const bareSection = line.match(SECTION_LINE);
        if (bareSection) {
            isTopLevelList = false;
            currentTitle = unquote(bareSection[1].trim());
            result[currentTitle] = [];
            continue;
        }
        // An inline scalar value: `Раздел: value`
        const inlineSection = line.match(/^([^\s#][^:]*):\s+(.+)$/);
        if (inlineSection) {
            isTopLevelList = false;
            currentTitle = unquote(inlineSection[1].trim());
            result[currentTitle] = unquote(inlineSection[2].trim());
            continue;
        }
        // A list item under the current section.
        const item = line.match(/^\s+-\s+(.*)$/);
        if (item && currentTitle !== null && Array.isArray(result[currentTitle])) {
            (result[currentTitle] as unknown[]).push(unquote(item[1].trim()));
            continue;
        }
        // A top-level list item: the document is not a mapping.
        const topLevelItem = line.match(/^-\s+(.*)$/);
        if (topLevelItem && currentTitle === null && Object.keys(result).length === 0) {
            continue; // stay a top-level list; the validator reports it
        }
        throw new Error("unsupported yaml");
    }
    if (isTopLevelList && Object.keys(result).length === 0) {
        return [];
    }
    return result;
}

/** Strip a single pair of matching quotes from a YAML scalar. */
function unquote(value: string): string {
    if (
        value.length >= 2 &&
        ((value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'")))
    ) {
        return value.slice(1, -1);
    }
    return value;
}
