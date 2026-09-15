/**
 * Remove the fetch header/body timeouts for the server-side backend proxy.
 *
 * Next's `fetch` runs on Node's bundled undici, whose defaults (300s headers,
 * 300s body) can abort a large DOCX round-trip before the backend answers.
 * The masking itself is fast, but upload + transfer of a sizable document
 * should never hit an arbitrary ceiling, so a global dispatcher disables the
 * two timeouts. `connectTimeout` stays at the default so a genuinely dead
 * backend still fails quickly.
 *
 * Runs once per server process (Node.js runtime only — this is a server file).
 */
export async function register(): Promise<void> {
    if (process.env.NEXT_RUNTIME !== "nodejs") return;
    const { Agent, setGlobalDispatcher } = await import("undici");
    setGlobalDispatcher(
        new Agent({
            headersTimeout: 0,
            bodyTimeout: 0,
        }),
    );
}
