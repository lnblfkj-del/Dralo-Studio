import { http } from "./client";

export interface SourceIssue {
  line: number;
  text: string;
  start: number;
  end: number;
  line_fingerprint: string;
  message: string;
  severity: "warning" | "error";
  resolvable: boolean;
}
export interface SourceReview {
  episode_id: number;
  script_revision: number;
  script_fingerprint: string;
  production_revision: number;
  production_ready: boolean;
  errors: SourceIssue[];
  unresolved_lines: number[];
}
export type SourceKind = "device" | "narration" | "anonymous" | "sound_effects" | "ambience";
export type SourceLocation = "on_screen" | "off_screen" | "subjective" | "unspecified";
export async function getSourceReview(project: number, episode: number): Promise<SourceReview> {
  return (await http.get<SourceReview>(`/projects/${project}/episodes/${episode}/source-review`)).data;
}
export async function confirmSource(project: number, review: SourceReview, issue: SourceIssue, kind: SourceKind, location: SourceLocation): Promise<SourceReview> {
  return (await http.put<SourceReview>(`/projects/${project}/episodes/${review.episode_id}/source-interpretations`, {
    expected_script_revision: review.script_revision,
    expected_production_revision: review.production_revision,
    script_fingerprint: review.script_fingerprint,
    line_fingerprint: issue.line_fingerprint,
    source_line: issue.line, kind, location, confirmed: true,
  })).data;
}
