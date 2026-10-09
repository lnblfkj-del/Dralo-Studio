import { fetchEventSource } from "@microsoft/fetch-event-source";

import { authenticationHeaders, http, sessionSignal } from "@/api/client";
import type { Job } from "@/types/api";

export type BulkJobAction = "cancel" | "retry" | "delete" | "restore" | "purge";

export interface BulkJobActionResult {
  job_id: number;
  outcome: "cancelled" | "queued" | "deleted" | "restored" | "purged" | "covered_by_parent" | "failed";
  job_status: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface BulkJobActionResponse {
  action: BulkJobAction;
  items: BulkJobActionResult[];
  succeeded: number;
  failed: number;
  scope: "selection" | "filter";
  matched: number;
}

export interface JobDiagnostic {
  job_id: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  attempts: number;
  max_attempts: number;
  queue_reason: string | null;
  worker_online: boolean;
  cancel_allowed: boolean;
  retry_allowed: boolean;
  retry_block_reason: string | null;
  paid_recall_allowed?: boolean;
  delete_allowed: boolean;
  execution_info: Job["execution_info"];
  model_runtime?: {
    model_id: number;
    configured_limit: number;
    effective_limit: number;
    active: number;
    rate_limit_until: string | null;
    rate_limit_hits: number;
  } | null;
}

export interface JobStats {
  total: number;
  active: number;
  queued: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  today_completed: number;
  recycled: number;
}

export interface JobListResponse {
  items: Job[];
  total: number;
  offset: number;
  limit: number;
  stats: JobStats;
}

export interface JobListOptions {
  offset?: number;
  limit?: number;
  projectId?: number;
  status?: string;
  jobType?: string;
  search?: string;
  sort?: "newest" | "oldest";
  recycled?: boolean;
}

export async function listJobsPage(options: JobListOptions = {}): Promise<JobListResponse> {
  return (await http.get<JobListResponse>("/jobs", { params: {
    offset: options.offset,
    limit: options.limit,
    project_id: options.projectId,
    status: options.status === "all" ? undefined : options.status,
    job_type: options.jobType === "all" ? undefined : options.jobType,
    search: options.search || undefined,
    sort: options.sort,
    recycled: options.recycled || undefined,
  } })).data;
}

export async function listJobs(): Promise<Job[]> {
  return (await listJobsPage()).items;
}

export async function createTextJob(payload: {
  provider_model_id: number;
  prompt: string;
  project_id?: number;
  parameters?: Record<string, unknown>;
}): Promise<Job> {
  return (await http.post<Job>("/jobs", payload)).data;
}

export async function createShotVideoJob(payload: {
  provider_model_id: number;
  shot_id: number;
  prompt: string;
  negative_prompt?: string | null;
  parameters?: Record<string, unknown>;
}): Promise<Job> {
  return (await http.post<Job>("/jobs/video/shot", payload)).data;
}

export async function createBatchVideoJob(payload: {
  project_id: number;
  provider_model_id: number;
  shot_ids: number[];
  parameters?: Record<string, unknown>;
}): Promise<Job> {
  return (await http.post<Job>("/jobs/video/batch", payload)).data;
}

export async function getJob(jobId: number): Promise<Job> {
  return (await http.get<Job>(`/jobs/${jobId}`)).data;
}

export async function listJobChildren(jobId: number): Promise<Job[]> {
  return (await http.get<Job[]>(`/jobs/${jobId}/children`)).data;
}

export async function cancelJob(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/cancel`)).data;
}

export async function retryJob(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/retry`)).data;
}

export interface OutlineCastReview {
  response_sha256: string;
  issues: { name: string; episodes: number[] }[];
  characters: { name: string; role: string | null; aliases: string[] }[];
}

export async function getOutlineCastReview(jobId: number): Promise<OutlineCastReview> {
  return (await http.get<OutlineCastReview>(`/jobs/${jobId}/outline-cast-review`)).data;
}

export async function reprocessJobResponse(jobId: number, correction?: {
  character_name_corrections: Record<string, string>;
  expected_response_sha256: string;
}): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/reprocess-response`, correction)).data;
}

export interface DirectorRecoveryState {
  job_id: number;
  completed_segments: number;
  expected_segments: number;
  free_job_ids: number[];
  paid_scopes: { job_id: number; label: string; provider: string | null; model: string | null; unknown_result: boolean; pricing_estimate?: { amount: string | null; currency: string; reason: string } }[];
  blocked_scopes: { job_id: number; label: string; reason: string }[];
  can_retry: boolean;
  confirmation_token: string | null;
  max_new_calls: number;
  unknown_result_count: number;
  fee_message: string;
  block_reason: string | null;
}

export async function getDirectorRecovery(jobId: number): Promise<DirectorRecoveryState> {
  return (await http.get<DirectorRecoveryState>(`/jobs/${jobId}/director-recovery`)).data;
}

export async function recoverDirectorResponses(jobId: number): Promise<{ job: Job; recovery: DirectorRecoveryState }> {
  return (await http.post<{ job: Job; recovery: DirectorRecoveryState }>(`/jobs/${jobId}/director-recovery`)).data;
}

export async function confirmDirectorRecovery(jobId: number, state: DirectorRecoveryState, acceptUnknownCharge: boolean): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/confirm-recall`, {
    acknowledge_new_model_call: true,
    confirmation_token: state.confirmation_token,
    channel_checked: false,
    accept_unknown_charge: acceptUnknownCharge,
    reason: "用户在当前页面确认只重试未完成范围及可能再次收费",
  })).data;
}

export async function confirmRecallJob(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/confirm-recall`, {
    acknowledge_new_model_call: true,
    channel_checked: true,
    reason: "用户在失败详情中选择重新生成失败范围",
  })).data;
}

export async function deleteJob(jobId: number): Promise<number[]> {
  return (await http.delete<{ deleted_ids: number[] }>(`/jobs/${jobId}`)).data.deleted_ids;
}

export async function restoreJob(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/restore`)).data;
}

export async function purgeJob(jobId: number): Promise<{ purged_ids: number[] }> {
  return (await http.delete<{ purged_ids: number[] }>(`/jobs/${jobId}/purge`)).data;
}

export async function pauseBatch(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/pause`)).data;
}

export async function resumeBatch(jobId: number): Promise<Job> {
  return (await http.post<Job>(`/jobs/${jobId}/resume`)).data;
}

export async function getJobDiagnostic(jobId: number): Promise<JobDiagnostic> {
  return (await http.get<JobDiagnostic>(`/jobs/${jobId}/diagnostic`)).data;
}

export async function bulkJobAction(
  jobIds: number[],
  action: BulkJobAction,
  options: JobListOptions & { scope?: "selection" | "filter" } = {},
): Promise<BulkJobActionResponse> {
  return (
    await http.post<BulkJobActionResponse>("/jobs/bulk-actions", {
      job_ids: jobIds,
      action,
      scope: options.scope ?? "selection",
      project_id: options.projectId,
      status: options.status === "all" ? undefined : options.status,
      job_type: options.jobType === "all" ? undefined : options.jobType,
      search: options.search || undefined,
      recycled: options.recycled ?? false,
    })
  ).data;
}

export function subscribeToJob(
  jobId: number,
  signal: AbortSignal,
  onJob: (job: Job) => void,
): Promise<void> {
  return fetchEventSource(`/api/jobs/${jobId}/events`, {
    signal: sessionSignal(signal),
    headers: authenticationHeaders(),
    openWhenHidden: true,
    onmessage(event) {
      if (event.event === "job") onJob(JSON.parse(event.data) as Job);
    },
    onopen(response) {
      if (!response.ok) throw new Error("任务事件连接失败");
      return Promise.resolve();
    },
  });
}
