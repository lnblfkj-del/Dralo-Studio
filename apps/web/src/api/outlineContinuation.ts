import { http } from "./client";

export type ProposedEpisode = { number: number; title: string; synopsis: string; dramatic_goal: string; cliffhanger: string; characters?: string[] };
export type OptimizationType = "pacing" | "conflict" | "detail" | "cliffhanger" | "concise" | "custom";
export type ContinuationRequest = { expected_revision: number; request_id: string; mode: "append" | "fill" | "optimize"; count: number; outline_keys: string[]; duration_seconds: number; direction: string; ending: string; story_ended: boolean; fixed_facts: string; optimization_types: OptimizationType[] };
type EvidenceNote = { text: string; sources: string[] };
export type ContinuationProposal = {
  id: number; revision: number; request: ContinuationRequest; status: string; stage: string; stale: boolean;
  targets: { number: number; outline_key: string; duration_seconds: number; preserved_fields?: Partial<ProposedEpisode>; original?: ProposedEpisode }[];
  completed: Record<string, ProposedEpisode>; job_id: number; job_ids: number[]; job_status: string; error?: string;
  context: { coverage: string; warnings: string[]; sources: { id: string; coverage: string; excerpt: string }[] };
  plan: { summary: string; character_states: EvidenceNote[]; unresolved_hooks: EvidenceNote[]; episode_beats: EvidenceNote[] } | null;
  review: { summary: string; issues: { category: string; severity: string; episodes: number[]; explanation: string; sources: string[] }[] } | null;
  applied_artifact_id?: number;
};
export type ProposalAction = { expected_revision: number; action: "resume" | "retry_episode" | "save" | "recheck" | "cancel" | "apply"; number?: number; episodes?: ProposedEpisode[]; update_planned_count?: boolean; acknowledge_issues?: boolean };
const base = (sid: number) => `/creation/sessions/${sid}`;
export async function listContinuations(sid: number) { return (await http.get<ContinuationProposal[]>(`${base(sid)}/outline-continuations`)).data; }
export async function startContinuation(sid: number, aid: number, payload: ContinuationRequest) { return (await http.post<ContinuationProposal>(`${base(sid)}/artifacts/${aid}/outline-continuations`, payload)).data; }
export async function actOnContinuation(sid: number, id: number, payload: ProposalAction) { return (await http.post<ContinuationProposal>(`${base(sid)}/outline-continuations/${id}/actions`, payload)).data; }
