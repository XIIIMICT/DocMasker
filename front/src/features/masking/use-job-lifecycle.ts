"use client";

import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";

import type { JobSession } from "@/features/masking/mask-requests";
import type { ModeState, StateByMode } from "@/features/masking/types";
import { ACTIVE_JOB_KEY, cancelJob, type JobAccepted, waitForJob } from "@/shared/api/jobs";
import type { MaskResponse } from "@/shared/api/mask";
import { statsFromAggregate } from "@/shared/mask-stats";
import type { AppMode } from "@/shared/ui/brand";

type Update = (patch: Partial<ModeState>) => void;

/** The full lifecycle surface; `JobSession` is the read-only slice for requests. */
export interface JobLifecycle extends JobSession {
    finish(): void;
    onCancelJob(): Promise<void>;
}

interface JobLifecycleInput {
    mode: AppMode;
    activeJob: JobAccepted | null;
    update: Update;
    setStateByMode: Dispatch<SetStateAction<StateByMode>>;
}

/**
 * Owns the active job's lifecycle: the sessionStorage handle, the polling
 * AbortController, cancellation and reload-resume. Only the opaque 202 body
 * (job id + status/result urls) is persisted — never input text, DOCX bytes,
 * keys, filenames or any other PII.
 */
export function useJobLifecycle({
    mode,
    activeJob,
    update,
    setStateByMode,
}: JobLifecycleInput): JobLifecycle {
    const pollingController = useRef<AbortController | null>(null);

    /** Remember the active job so Cancel and reload-resume can find it. */
    async function trackAccepted(response: Response): Promise<JobAccepted> {
        const accepted = (await response.json()) as JobAccepted;
        update({ activeJob: accepted });
        try {
            window.sessionStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ mode, accepted }));
        } catch {
            /* private mode: in-memory state still supports Cancel this session */
        }
        return accepted;
    }

    /** Drop the active job after a terminal state or explicit dismissal. */
    function endJob() {
        update({ activeJob: null });
        try {
            window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
        } catch {
            /* ignore */
        }
    }

    /** Release polling state after any run (success, failure or cancellation). */
    function finish() {
        pollingController.current = null;
        endJob();
        update({ busy: false });
    }

    /** Cancel the server-side job (AbortController only stops local polling). */
    async function onCancelJob() {
        if (!activeJob) return;
        const outcome = await cancelJob(activeJob);
        if (outcome.kind === "accepted") {
            pollingController.current?.abort();
            endJob();
            update({ busy: false, error: "Задание отменено." });
            return;
        }
        if (outcome.kind === "already_terminal") {
            // The job won the race and finished before the DELETE arrived.
            pollingController.current?.abort();
            endJob();
            update({ busy: false, error: "Задание уже завершено. Проверьте результат." });
            return;
        }
        // Server/network failure: keep the active job and polling so the user
        // can retry; never claim the worker was stopped.
        update({ error: "Не удалось подтвердить отмену задания. Повторите попытку." });
    }

    // Resume an active job after a reload in the same tab (§3.11). Only the
    // opaque job id lives in sessionStorage — never input documents, keys or
    // PII. A 404/expired/failed/cancelled job is handled by waitForJob.
    // The stored mode is the mode the operation started in, so the result is
    // restored into that draft even if the user has switched tabs meanwhile.
    useEffect(() => {
        let stored: { mode: AppMode; accepted: JobAccepted } | null = null;
        try {
            const raw = window.sessionStorage.getItem(ACTIVE_JOB_KEY);
            if (raw) stored = JSON.parse(raw) as { mode: AppMode; accepted: JobAccepted };
        } catch {
            /* ignore malformed/private-mode storage */
        }
        if (!stored) return;
        const { mode: jobMode, accepted } = stored;
        setStateByMode((current) => ({
            ...current,
            [jobMode]: { ...current[jobMode], activeJob: accepted, busy: true },
        }));
        const controller = new AbortController();
        void waitForJob(accepted, controller.signal)
            .then(async (result) => {
                // File jobs return a ZIP without result.json; resume can only
                // repopulate text results — clear the active job and let the
                // user download from the result URL if they need the file.
                const jsonEntry = result.entry(".json");
                if (!jsonEntry) {
                    setStateByMode((current) => ({
                        ...current,
                        [jobMode]: {
                            ...current[jobMode],
                            activeJob: null,
                            busy: false,
                            error: "Задание завершено. Повторите операцию, чтобы скачать файл.",
                        },
                    }));
                    try {
                        window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                    } catch {
                        /* ignore */
                    }
                    return;
                }
                const payload = JSON.parse(await jsonEntry.text()) as {
                    text?: string;
                    keys?: string;
                    stats?: MaskResponse["stats"];
                    kind?: string;
                };
                setStateByMode((current) => {
                    const slice = { ...current[jobMode], activeJob: null, busy: false };
                    if (jobMode === "mask" && payload.stats !== undefined) {
                        return {
                            ...current,
                            [jobMode]: {
                                ...slice,
                                result: payload.text ?? "",
                                keys: payload.keys ?? "",
                                stats: statsFromAggregate(payload.stats),
                            },
                        };
                    }
                    return { ...current, [jobMode]: { ...slice, restored: payload.text ?? "" } };
                });
                try {
                    window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                } catch {
                    /* ignore */
                }
            })
            .catch((error: unknown) => {
                setStateByMode((current) => ({
                    ...current,
                    [jobMode]: {
                        ...current[jobMode],
                        activeJob: null,
                        busy: false,
                        error:
                            error instanceof Error
                                ? error.message
                                : "Не удалось обработать задание.",
                    },
                }));
                try {
                    window.sessionStorage.removeItem(ACTIVE_JOB_KEY);
                } catch {
                    /* ignore */
                }
            });
        return () => controller.abort();
    }, [setStateByMode]);

    // A page unmount cannot wait for a running job. Stop local polling (and
    // any in-flight unpack/checks) without cancelling the server job: only a
    // confirmed DELETE does that. The resume effect owns its own controller.
    useEffect(
        () => () => {
            const controller = pollingController.current;
            pollingController.current = null;
            controller?.abort();
        },
        [],
    );

    return { trackAccepted, endJob, finish, pollingController, onCancelJob };
}
