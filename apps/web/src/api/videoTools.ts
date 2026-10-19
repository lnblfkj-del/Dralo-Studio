import { http } from "./client";
import type { ProviderProtocol, VideoConfigInput } from "@/types/provider";
import type { Job } from "@/types/api";
import type { PriceQuote } from "./providers";

export interface VideoPackage { schema_version: "video-package.v1"; name: string; protocol: ProviderProtocol; video_config: VideoConfigInput }
export interface VideoPreset {
  key: string; name: string; version: string; source: string; model_ids: string[];
  protocol: ProviderProtocol; package: VideoPackage; fingerprint: string;
  origin_provider_id: number | null; read_only: true; verification: "declared";
}
export async function listVideoPresets(): Promise<{items: VideoPreset[]}> {
  return (await http.get("/providers/video-config/catalog")).data;
}
export async function previewVideoPreset(value: {key: string; fingerprint: string; provider_id: number; model_identifier: string}): Promise<VideoPreset & {confirm_interface: boolean}> {
  return (await http.post("/providers/video-config/catalog/preview", value)).data;
}
export async function saveVideoPreset(value: {name: string; version: string; source: string; model_ids: string[]; package: VideoPackage}): Promise<VideoPreset> {
  return (await http.post("/providers/video-config/catalog", value)).data;
}
export interface VideoCheck { ready: boolean; model_called: false; issues: string[]; modes: {key: string; input_mode: string; passed: boolean; issues: string[]}[] }
export interface TrialInput {project_id: number; prompt: string; parameters: Record<string, unknown>; first_frame_media_id?: number; last_frame_media_id?: number; reference_media_ids: number[]}
export interface TrialPlan {confirmation_token: string; quote: PriceQuote; parameters: Record<string, unknown>; input_mode: string}
export interface TrialRecord {id: number; status: string; error: string | null; result: Record<string, unknown> | null; test: {parameters: Record<string, unknown>; input_mode: string}; created_at: string; can_resume: boolean}
const root = (provider: number, model: number) => `/providers/${provider}/models/${model}`;

export async function getVideoTemplates(): Promise<{items: {id: ProviderProtocol; name: string}[]; custom_example: Record<string, unknown>}> {
  return (await http.get("/providers/video-config/templates")).data;
}
export async function importVideoPackage(value: unknown): Promise<VideoPackage> {
  return (await http.post("/providers/video-config/import", value)).data;
}
export async function importVideoLink(url: string): Promise<VideoPackage> {
  return (await http.post("/providers/video-config/import-link", {url})).data;
}
export async function checkVideoConfig(provider: number, model: number, draft: {api_protocol: ProviderProtocol | null; api_base_url: string | null; model_id: string; video_config: VideoConfigInput}): Promise<VideoCheck> {
  return (await http.post(root(provider, model) + "/video-check", draft)).data;
}
export async function planVideoTrial(provider: number, model: number, value: TrialInput): Promise<TrialPlan> {
  return (await http.post(root(provider, model) + "/video-test/plan", value)).data;
}
export async function startVideoTrial(provider: number, model: number, value: TrialInput & {request_id: string; confirmation_token: string; confirmed: true; max_cost_cents: number; accept_unknown_price: boolean}): Promise<Job> {
  return (await http.post(root(provider, model) + "/video-test/start", value)).data;
}
export async function listVideoTrials(provider: number, model: number): Promise<TrialRecord[]> {
  return (await http.get(root(provider, model) + "/video-test/history")).data;
}

export async function downloadVideoTemplate(kind: "json" | "service") {
  return (await http.get(`/providers/video-config/template/${kind}`)).data as VideoPackage;
}
export async function getVideoImportAssistance(): Promise<{schema_version: string; prompt: string; example: VideoPackage}> {
  return (await http.get('/providers/video-config/assistant')).data;
}
export interface VideoVersion {id: number; created_at: string; account_id: number; protocol: string; model_identifier: string; current: boolean; fingerprint: string}
export interface VideoHistory {expected_fingerprint: string; items: VideoVersion[]}
export async function videoHistory(provider: number, model: number): Promise<VideoHistory> {
  return (await http.get(root(provider, model) + "/video-versions")).data;
}
export async function rollbackVideoVersion(provider: number, model: number, version: number, fingerprint: string) {
  return (await http.post(root(provider, model) + "/video-versions/rollback", {version_id: version, expected_fingerprint: fingerprint})).data;
}
export interface AdapterRelease {id: number; name: string; adapter_key: string; version: string; enabled: boolean}
export async function listVideoAdapters(): Promise<AdapterRelease[]> { return (await http.get("/providers/video-adapters")).data; }
export async function installVideoAdapter(provider: number, value: VideoPackage) {
  return (await http.post(`/providers/${provider}/video-adapters/install`, {package: value, trust_service: true})).data;
}
export async function setVideoAdapterEnabled(id: number, enabled: boolean) {
  return (await http.patch(`/providers/video-adapters/${id}`, {enabled})).data;
}
export interface VideoAccount {id: number; current: boolean; key_hint: string; created_at: string}
export async function listVideoAccounts(provider: number): Promise<VideoAccount[]> {
  return (await http.get(`/providers/${provider}/video-accounts`)).data;
}
export async function rotateVideoAccount(provider: number, account: number, key: string) {
  return (await http.post(`/providers/${provider}/video-accounts/${account}/rotate-key`, {api_key: key, confirm_same_account: true})).data;
}
