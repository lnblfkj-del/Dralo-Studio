import { http } from "@/api/client";

export interface ExecutionHealth {
  execution_location?: "local" | "cloud";
  status: string;
  ready: boolean;
  active_workers: number;
  compatible_workers: number;
  version_mismatch_workers: number;
  runtime_version: string;
  concurrency_preset?: "low" | "standard" | "high";
  concurrency_limits?: Record<string, number>;
  total_capacity: number;
  active_jobs: number;
  available_capacity: number;
  queued_jobs: number;
  remote_jobs: number;
  blocked_queued_jobs: number;
  can_process_queue: boolean;
  heartbeat_grace_seconds: number;
}

export interface SystemHealth {
  status: string;
  app_env: string;
  database: string;
  storage_free_gb: number;
  storage_warning?: string | null;
  execution: ExecutionHealth;
}

export async function getSystemHealth(): Promise<SystemHealth> {
  return (await http.get<SystemHealth>("/health")).data;
}
