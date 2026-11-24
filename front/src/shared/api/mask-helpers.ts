/**
 * Pure helpers shared by the masking/demasking workflows.
 *
 * No React imports: these adapters turn backend/job responses into the shapes
 * the UI panes expect. File results use `manifest.json` as the single source
 * of statistics/coverage (§7); no header parsing or placeholder counting.
 */

import type { JobAccepted, JobResult } from "@/shared/api/jobs";
import { waitForJob } from "@/shared/api/jobs";
import type { MaskResponse, UnmaskResponse } from "@/shared/api/mask";
import {
    MANIFEST_NAME,
    maskBundleFromEntries,
    restoreBundleFromEntries,
    validateManifestText,
    type MaskedBundle,
    type RestoredBundle,
} from "@/shared/api/mask-archive";

/** Convert a text-mask job's result.json into the sync MaskResponse shape. */
export async function jobToMaskResponse(
    accepted: JobAccepted,
    signal?: AbortSignal,
): Promise<MaskResponse> {
    // §7.3: the inner DTO is the exact canonical MaskResponse (transport only).
    const payload = await (await waitForJob(accepted, signal)).json<MaskResponse>();
    return payload;
}

export async function jobToUnmaskResponse(
    accepted: JobAccepted,
    signal?: AbortSignal,
): Promise<UnmaskResponse> {
    return (await waitForJob(accepted, signal)).json<UnmaskResponse>();
}

/** Build the mask bundle from a file job's already-unpacked flat result ZIP. */
export async function jobBundle(result: JobResult): Promise<MaskedBundle> {
    const manifestEntry = result.entries.get(MANIFEST_NAME);
    if (!manifestEntry) throw new Error("Отчёт о результате отсутствует.");
    const manifest = validateManifestText(await manifestEntry.text(), "mask_file");
    return maskBundleFromEntries(result.entries, manifest);
}

/** Build the restored-file bundle from an unmask_file job result ZIP. */
export async function jobRestoreBundle(
    accepted: JobAccepted,
    signal?: AbortSignal,
): Promise<RestoredBundle> {
    const result = await waitForJob(accepted, signal);
    const manifestEntry = result.entries.get(MANIFEST_NAME);
    if (!manifestEntry) throw new Error("Отчёт о результате отсутствует.");
    const manifest = validateManifestText(await manifestEntry.text(), "unmask_file");
    return restoreBundleFromEntries(result.entries, manifest);
}

export type { MaskedBundle, RestoredBundle };
