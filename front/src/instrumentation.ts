/**
 * Bounded server-side fetch timeouts (ADR-0002, F-08).
 *
 * The old global dispatcher disabled undici's header/body timeouts for EVERY
 * server-side fetch (0 = unbounded), which could leave hung requests forever.
 * Long work now runs through the durable job API, so the proxy only needs
 * finite, generous-but-bounded limits: 60 s to first byte, 10 min for a body.
 * Short job/polling routes use a per-request AbortSignal.timeout instead.
 * connectTimeout keeps its default so a dead backend still fails quickly.
 *
 * Runs once per server process (Node.js runtime only — this is a server file).
 */
export async function register(): Promise<void> {
    if (process.env.NEXT_RUNTIME !== "nodejs") return;
    const { Agent, setGlobalDispatcher } = await import("undici");
    setGlobalDispatcher(
        new Agent({
            headersTimeout: 60_000,
            bodyTimeout: 600_000,
        }),
    );
}
