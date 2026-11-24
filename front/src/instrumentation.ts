/**
 * Bounded global server-side fetch timeouts (§8.4 safety net).
 *
 * The old global dispatcher disabled undici's header/body timeouts for EVERY
 * server-side fetch (0 = unbounded), which could leave hung requests forever.
 * The canonical per-route policy now lives in `shared/api/timeouts.ts`
 * (15 s JSON / job, 120 s upload, 300 s result); `postToBackend` applies it
 * via `AbortSignal.timeout`. This global Agent is only a coarse outer bound:
 * 60 s to first byte and 10 min for a body — finite, never 0/unbounded.
 * connectTimeout keeps its default so a dead backend still fails quickly.
 * Long work runs through the durable job API, never a held-open connection.
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
