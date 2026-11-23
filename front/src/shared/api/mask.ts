import { getBackendInternalUrl } from "@/shared/config";

/** Aggregate counts the backend sends instead of raw entity values. */
export interface MaskStatsPayload {
    total: number;
    occurrences_by_label: Record<string, number>;
    unique_by_label: Record<string, number>;
}

export interface MaskResponse {
    text: string;
    /** Aggregate counts per label — the backend never sends raw entity values. */
    stats: MaskStatsPayload;
    /** Demasking keys as YAML (empty string when nothing was masked). */
    keys: string;
}

export interface UnmaskResponse {
    text: string;
}

/**
 * Russian section titles accepted in a demasking keys file. Must match the
 * backend's registered titles (see `docs/adr/0001-masking-profiles.md`).
 */
export const KEYS_SECTION_TITLES = [
    "Физлица",
    "Номера телефонов",
    "Email",
    "Ссылки",
    "Организации",
    "Адреса",
] as const;

/** The single metadata key of a keys file (`Профиль`); not a section. */
const KEYS_ENVELOPE_KEYS = ["Профиль"] as const;

/** Russian keys section title → entity label (mirrors the backend `LABEL_TITLES`). */
export const KEYS_TITLE_LABELS: Record<string, string> = {
    Физлица: "person",
    "Номера телефонов": "phone",
    Email: "email",
    Ссылки: "link",
    Организации: "organization",
    Адреса: "address",
};

/**
 * The placeholders a keys document stands for, e.g. `["[PERSON_1]", …]`.
 *
 * Numbers restart at 1 within each section (that is how the backend writes
 * them), so the n-th value of a section is `[LABEL_n]`. Unparseable input
 * yields an empty list rather than throwing — highlighting is a nicety.
 */
export function placeholdersFromKeys(text: string): string[] {
    return keyEntries(text).map(([placeholder]) => placeholder);
}

/** The original values a keys document carries, in order. */
export function valuesFromKeys(text: string): string[] {
    return keyEntries(text).map(([, value]) => value);
}

/** `(placeholder, value)` pairs of a keys document, in document order. */
export function keyEntries(text: string): Array<[string, string]> {
    return keysEntries(text).map(([label, index, value]) => [
        `[${label.toUpperCase()}_${index}]`,
        value,
    ]);
}

/** `(label, number, value)` triples of a keys document, in document order. */
function keysEntries(text: string): Array<[string, number, string]> {
    let parsed: unknown;
    try {
        parsed = parseSimpleYaml(text);
    } catch {
        return [];
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
        return [];
    }
    const entries: Array<[string, number, string]> = [];
    for (const [title, values] of Object.entries(parsed as Record<string, unknown>)) {
        const label = KEYS_TITLE_LABELS[title];
        if (label === undefined || !Array.isArray(values)) continue;
        values.forEach((value, index) => {
            if (typeof value === "string" && value.length > 0) {
                entries.push([label, index + 1, value]);
            }
        });
    }
    return entries;
}

/**
 * Validate a demasking keys file on the client, mirroring the backend rules
 * (a mapping of known Russian sections to non-empty lists of non-empty
 * strings). Returns an error message in Russian, or `null` when valid — the
 * form uses it to block the request and mark the field invalid.
 */
export function validateKeysYaml(text: string): string | null {
    if (text.trim().length === 0) {
        return "Файл ключей пуст.";
    }
    // YAML keeps only the last of repeated mapping keys, so a duplicated section
    // is invisible after parsing — detect it from the raw lines first.
    const seenSections = new Set<string>();
    for (const line of text.split(/\r?\n/)) {
        const match = line.match(/^([^\s#][^:]*):\s*$/);
        if (match === null) continue;
        const title = match[1].trim();
        if ((KEYS_ENVELOPE_KEYS as readonly string[]).includes(title)) continue;
        if (seenSections.has(title)) {
            return `Раздел «${title}» встречается в файле больше одного раза.`;
        }
        seenSections.add(title);
    }
    let parsed: unknown;
    try {
        parsed = parseSimpleYaml(text);
    } catch {
        return "Файл ключей не является корректным YAML.";
    }
    if (parsed === null || parsed === undefined) {
        return "Файл ключей пуст.";
    }
    if (typeof parsed !== "object" || Array.isArray(parsed)) {
        return "Файл ключей должен содержать словарь разделов.";
    }
    // A keys file may carry a `Профиль` marker before the sections; it is
    // metadata only (a file without it restores as the default profile).
    // No version keys exist — the format has one current shape, so any other
    // top-level key is an unknown section and fails the checks below.
    const record = parsed as Record<string, unknown>;
    const hasEnvelope = KEYS_ENVELOPE_KEYS.some((key) => key in record);
    const entries = Object.entries(record).filter(
        ([title]) => !(KEYS_ENVELOPE_KEYS as readonly string[]).includes(title),
    );
    if (entries.length === 0) {
        return hasEnvelope
            ? "Файл ключей содержит метаданные, но не содержит ни одного раздела."
            : "Файл ключей не содержит ни одного раздела.";
    }
    for (const [title, values] of entries) {
        if (!(KEYS_SECTION_TITLES as readonly string[]).includes(title)) {
            const known = KEYS_SECTION_TITLES.map((name) => `«${name}»`).join(", ");
            return `Неизвестный раздел ключей: «${title}». Ожидаются: ${known}.`;
        }
        if (!Array.isArray(values)) {
            return `Раздел «${title}» должен быть списком значений.`;
        }
        if (!values.every((value) => typeof value === "string" && value.trim().length > 0)) {
            return `Раздел «${title}» должен содержать непустые строки.`;
        }
        if (new Set(values).size !== values.length) {
            return `Раздел «${title}» содержит дубликаты значений.`;
        }
    }
    return null;
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
        const bareSection = line.match(/^([^\s#][^:]*):\s*$/);
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

/** Short timeout for JSON job/status requests (polling must fail fast). */
export const PROXY_JSON_TIMEOUT_MS = 15_000;
/** Generous but bounded timeout for the synchronous file endpoints. */
export const PROXY_FILE_TIMEOUT_MS = 600_000;

/**
 * Forward a text unmask request to the backend (server-side, no CORS).
 * Used by the Next.js API route `/api/unmask`. The response body is streamed
 * through (no full arrayBuffer copy) so large payloads are not buffered twice.
 */
export async function proxyUnmaskRequest(text: string, keys: string): Promise<Response> {
    const targetUrl = new URL("/api/unmask", getBackendInternalUrl());

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text, keys }),
        signal: AbortSignal.timeout(PROXY_FILE_TIMEOUT_MS),
    });

    const responseContentType = response.headers.get("Content-Type") ?? "application/json";
    const headers = new Headers({ "Content-Type": responseContentType });
    return new Response(response.body, { headers, status: response.status });
}

/**
 * Forward a mask request to the backend (server-side, no CORS).
 * Used by the Next.js API route `/api/mask`. Returns the backend's HTTP
 * response verbatim (status + streamed body).
 */
export async function proxyMaskRequest(text: string): Promise<Response> {
    const targetUrl = new URL("/api/mask", getBackendInternalUrl());

    const response = await fetch(targetUrl, {
        cache: "no-store",
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ text }),
        signal: AbortSignal.timeout(PROXY_FILE_TIMEOUT_MS),
    });

    const responseContentType = response.headers.get("Content-Type") ?? "application/json";
    const headers = new Headers({ "Content-Type": responseContentType });
    return new Response(response.body, { headers, status: response.status });
}
