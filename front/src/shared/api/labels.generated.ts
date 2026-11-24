// GENERATED FILE — do not edit. Source: back/config/masking/labels.yaml
// Regenerate with: bun run --cwd front gen:labels
/** Canonical label ids → Russian keys-section titles, in output order. */
export const LABEL_CATALOGUE: ReadonlyArray<{ id: string; title: string }> = [
    { id: "person", title: "Физлица" },
    { id: "phone", title: "Номера телефонов" },
    { id: "email", title: "Email" },
    { id: "link", title: "Ссылки" },
    { id: "organization", title: "Организации" },
    { id: "address", title: "Адреса" },
] as const;

export const KEYS_SECTION_TITLES: readonly string[] = LABEL_CATALOGUE.map((entry) => entry.title);

export const KEYS_TITLE_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
    LABEL_CATALOGUE.map((entry) => [entry.title, entry.id]),
);
