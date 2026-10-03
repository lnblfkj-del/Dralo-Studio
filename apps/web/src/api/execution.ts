import { http } from "@/api/client";

export type ExecutionPreset = "low" | "standard" | "high" | "custom";

export interface ExecutionPolicy {
  preset: ExecutionPreset;
  concurrency: Record<"worker" | "global" | "text" | "image" | "video" | "tts" | "other", number>;
  retry: {
    paid_remote_auto_retries: number;
    connection_pre_send_retries: number;
    local_retry_backoff_seconds: number;
  };
  timeouts: {
    first_byte_seconds: number;
    stream_idle_seconds: number;
    task_deadline_seconds: number;
  };
  text_response_retention_days: number;
}

export interface ExecutionSettings {
  revision: number;
  policy: ExecutionPolicy;
  updated_at: string;
  updated_by: number | null;
  restart_required: boolean;
  restart_guard: {
    allowed: boolean;
    execution_location: string;
    supervisor_available: boolean;
    active_job_ids: number[];
    result_review_job_ids: number[];
    active_jobs: number;
    result_review_jobs: number;
  };
  runtime: {
    status: string;
    active_workers: number;
    total_capacity: number;
    active_jobs: number;
    available_capacity: number;
    queued_jobs: number;
    workers?: Array<{id: string; kind: string; policy_revision: number; max_concurrency: number; active_jobs: number; started_at?: string}>;
  };
}

export type WorkerRestartState = "requested" | "restarting" | "worker_started" | "completed" | "failed" | "timed_out";

export interface WorkerRestartStatus {
  request_id: string;
  state: WorkerRestartState;
  requested_at: string;
  expected_policy_revision: number;
  updated_at: string;
  message?: string;
  worker_id?: string;
  completed_at?: string;
}

export async function getExecutionSettings(): Promise<ExecutionSettings> {
  return (await http.get<ExecutionSettings>("/execution-settings")).data;
}

export async function updateExecutionSettings(revision: number, policy: ExecutionPolicy): Promise<ExecutionSettings> {
  return (await http.put<ExecutionSettings>("/execution-settings", { revision, policy })).data;
}

export async function restartWorker(): Promise<WorkerRestartStatus> {
  return (await http.post<WorkerRestartStatus>("/execution-settings/worker/restart")).data;
}

export async function getWorkerRestartStatus(requestId: string): Promise<WorkerRestartStatus> {
  return (await http.get<WorkerRestartStatus>(`/execution-settings/worker/restart/${requestId}`)).data;
}
