// GENERATED FILE — do not edit.
// Source: live app.openapi() exported by back/src/openapi_export.py
// Regenerate with: bun run --cwd front gen:openapi
/* eslint-disable */
/* tslint:disable */
// @ts-nocheck
/*
 * ---------------------------------------------------------------
 * ## THIS FILE WAS GENERATED VIA SWAGGER-TYPESCRIPT-API        ##
 * ##                                                           ##
 * ## AUTHOR: acacode                                           ##
 * ## SOURCE: https://github.com/acacode/swagger-typescript-api ##
 * ---------------------------------------------------------------
 */

/**
 * ApiErrorResponse
 * The one error envelope documented in OpenAPI; never carries submitted data.
 */
export interface ApiErrorResponse {
    /**
     * Code
     * Stable machine-readable error code.
     */
    code: string;
    /**
     * Detail
     * Russian user-facing detail; no PII, keys or local paths.
     */
    detail: string;
    /**
     * Request Id
     * Opaque id, identical to the X-Request-ID header.
     */
    request_id: string;
}

/** Body_mask_file_endpoint_api_mask_file_post */
export interface BodyMaskFileEndpointApiMaskFilePost {
    /**
     * File
     * DOCX file to mask
     */
    file: File | Blob;
}

/** Body_submit_job_api_jobs_post */
export interface BodySubmitJobApiJobsPost {
    /**
     * File
     * DOCX file to mask/restore
     */
    file?: File | Blob | null;
    /**
     * Filename
     * Original file name for the result archive
     */
    filename?: string | null;
    /**
     * Keys
     * Demasking keys YAML (unmask only)
     */
    keys?: string | null;
    /**
     * Kind
     * Job kind: mask_text | mask_file | unmask_text | unmask_file
     * @default "mask_text"
     */
    kind?: "mask_text" | "mask_file" | "unmask_text" | "unmask_file";
    /**
     * Text
     * Text to mask/restore
     */
    text?: string | null;
}

/** Body_unmask_file_endpoint_api_unmask_file_post */
export interface BodyUnmaskFileEndpointApiUnmaskFilePost {
    /**
     * File
     * Masked DOCX file to restore
     */
    file: File | Blob;
    /**
     * Keys
     * Demasking keys as YAML
     */
    keys: string;
}

/**
 * HealthResponse
 * Liveness body; no dependency state is exposed.
 */
export interface HealthResponse {
    /** Status */
    status: "ok";
}

/**
 * JobAccepted
 * Response to a submitted job (202).
 */
export interface JobAccepted {
    /** Job Id */
    job_id: string;
    /**
     * Result Url
     * Download the result here once succeeded.
     */
    result_url: string;
    /** Status */
    status:
        | "staging"
        | "queued"
        | "running"
        | "cancelling"
        | "succeeded"
        | "failed"
        | "cancelled"
        | "expired";
    /**
     * Status Url
     * Poll this URL for status/progress.
     */
    status_url: string;
}

/**
 * JobStatusResponse
 * Client-visible job state — never carries input values, keys or errors text.
 */
export interface JobStatusResponse {
    /** Attempt */
    attempt: number;
    /**
     * Cancel Requested
     * @default false
     */
    cancel_requested?: boolean;
    /** Created At */
    created_at?: number | null;
    /** Error Code */
    error_code?: string | null;
    /** Finished At */
    finished_at?: number | null;
    /** Job Id */
    job_id: string;
    /** Phase */
    phase:
        | "queued"
        | "validation"
        | "detection"
        | "rewrite"
        | "archive"
        | "completed"
        | "cancelled";
    /**
     * Progress
     * @min 0
     * @max 100
     */
    progress: number;
    /** Result Url */
    result_url?: string | null;
    /** Status */
    status:
        | "staging"
        | "queued"
        | "running"
        | "cancelling"
        | "succeeded"
        | "failed"
        | "cancelled"
        | "expired";
}

/**
 * LabelInfo
 * One canonical label (id + Russian keys-section title).
 */
export interface LabelInfo {
    /** Id */
    id: string;
    /** Title */
    title: string;
}

/**
 * LabelsResponse
 * The single canonical label catalogue, in output order.
 */
export interface LabelsResponse {
    /** Labels */
    labels: LabelInfo[];
    /** Schema Version */
    schema_version: number;
}

/**
 * MaskRequest
 * Incoming text to mask.
 */
export interface MaskRequest {
    /**
     * Text
     * Source text to mask sensitive information in.
     * @minLength 1
     */
    text: string;
}

/**
 * MaskResponse
 * The masked text, aggregate statistics and the demasking keys (YAML).
 *
 * Raw entity spans (original values + offsets) are deliberately not part of
 * the response: the UI only needs aggregates, so original PII values are not
 * duplicated over the wire (the keys file already carries the originals).
 */
export interface MaskResponse {
    /**
     * Keys
     * Demasking keys as YAML (empty when nothing was masked).
     */
    keys: string;
    /** Aggregate counts per label — the client never receives raw entity values. */
    stats: MaskStats;
    /** Text */
    text: string;
}

/**
 * MaskStats
 * Aggregate counts per label — the client never receives raw entity values.
 */
export interface MaskStats {
    /**
     * Occurrences By Label
     * Masked occurrence count per label.
     */
    occurrences_by_label: Record<string, number>;
    /**
     * Total
     * Total masked occurrences across all labels.
     */
    total: number;
    /**
     * Unique By Label
     * Distinct masked values per label.
     */
    unique_by_label: Record<string, number>;
}

/**
 * NotReadyResponse
 * Readiness body on dependency failure; never a DSN or DB message.
 */
export interface NotReadyResponse {
    /**
     * Reason
     * Safe Russian reason; no DSN, path or exception text.
     */
    reason: string;
    /** Status */
    status: "not_ready";
}

/**
 * ReadyResponse
 * Readiness body when PostgreSQL is reachable.
 */
export interface ReadyResponse {
    /**
     * Labels
     * Number of canonical labels in the loaded config.
     */
    labels: number;
    /** Status */
    status: "ready";
}

/**
 * UnmaskRequest
 * A masked text plus the keys that restore it.
 */
export interface UnmaskRequest {
    /**
     * Keys
     * Demasking keys as YAML.
     * @minLength 1
     */
    keys: string;
    /**
     * Text
     * Masked text to restore.
     * @minLength 1
     */
    text: string;
}

/**
 * UnmaskResponse
 * The restored text.
 */
export interface UnmaskResponse {
    /** Text */
    text: string;
}
