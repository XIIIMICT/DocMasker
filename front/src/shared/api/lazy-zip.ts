/** The JSZip constructor (jszip uses CommonJS `export =`). */
type JsZipClass = typeof import("jszip");

/**
 * Create a single-flight loader for the JSZip constructor. The loader is
 * shared by every client-side ZIP path so the library is downloaded (and
 * parsed) once, on first use, instead of sitting in the initial text-only
 * route chunk.
 *
 * A failed import is cached as a rejection: one broken chunk must not turn
 * into a retry storm on every keystroke.
 */
export function createJsZipLoader(importModule: () => Promise<JsZipClass>) {
    let cached: Promise<JsZipClass> | null = null;
    return function loadJsZip(): Promise<JsZipClass> {
        return (cached ??= importModule());
    };
}

/** The single shared loader used by `mask-archive` and `placeholder-count`. */
export const loadJsZip = createJsZipLoader(async () => {
    try {
        const module = await import("jszip");
        // CJS interop exposes module.exports as `default` in webpack/Turbopack
        // and Bun; fall back to the namespace for a plain ESM resolution.
        const interop = module as unknown as { default?: JsZipClass };
        return interop.default ?? (module as unknown as JsZipClass);
    } catch {
        // A chunk URL is a build artifact, not a user-facing reason.
        throw new Error("Не удалось загрузить модуль обработки архивов.");
    }
});
