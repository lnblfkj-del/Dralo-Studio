import { http } from "@/api/client";
import type { CreationArtifact, CreationSession, EpisodeOutlineContent, Job, NarrativeSpec, Project, ReferenceChunk, SceneShotDraftContent, SceneShotPublishResult, ScriptAssetCandidate, StoryBibleContent } from "@/types/api";

export async function createCreationSession(payload: {
  title: string;
  brief: string;
  settings?: Record<string, unknown>;
}): Promise<CreationSession> {
  return (await http.post<CreationSession>("/creation/sessions", payload)).data;
}

export async function getCreationSession(sessionId: number): Promise<CreationSession> {
  return (await http.get<CreationSession>(`/creation/sessions/${sessionId}`)).data;
}

export async function recoverLegacyCreationSession(sessionId: number): Promise<Project> {
  return (await http.post<Project>(`/creation/sessions/${sessionId}/recover`)).data;
}

export async function getProjectCreationSession(projectId: number): Promise<CreationSession> {
  return (await http.get<CreationSession>(`/projects/${projectId}/creation-session`, { params: { compact: true } })).data;
}

export async function getCreationArtifact(sessionId: number, artifactId: number): Promise<CreationArtifact> {
  return (await http.get<CreationArtifact>(`/creation/sessions/${sessionId}/artifacts/${artifactId}`)).data;
}

export async function listReferenceChunks(projectId: number): Promise<ReferenceChunk[]> {
  return (await http.get<ReferenceChunk[]>(`/projects/${projectId}/creation-session/chunks`)).data;
}

export type OutlineAgentAttachment = { name: string; content: string };

export type CharacterCompletionField = "age" | "description" | "personality" | "appearance" | "costume" | "voice";

export type CharacterBatchCompletion = {
  artifact_id: number;
  expected_revision: number;
  targets: Array<{
    character_index: number;
    character_id?: string;
    fields: CharacterCompletionField[];
  }>;
};

export type CharacterOutlineCoverage = {
  story_artifact_id: number;
  story_expected_revision: number;
  outline_artifact_id: number;
  outline_expected_revision: number;
};

export type StorySectionAdjustment = {
  artifact_id: number;
  expected_revision: number;
};

export async function runOutlineAgent(
  projectId: number,
  message: string,
  attachmentChunkIds: number[],
  attachments: OutlineAgentAttachment[] = [],
  characterOutlineCoverage?: CharacterOutlineCoverage,
  characterBatchCompletion?: CharacterBatchCompletion,
  storyOverviewAdjustment?: StorySectionAdjustment,
  eventTimelineAdjustment?: StorySectionAdjustment,
  optimizeAll = false,
): Promise<Job> {
  return (await http.post<Job>(`/projects/${projectId}/creation-session/agent`, {
    message,
    attachment_chunk_ids: attachmentChunkIds,
    attachments,
    ...(optimizeAll ? { optimize_all: true } : {}),
    ...(characterOutlineCoverage ? { character_outline_coverage: characterOutlineCoverage } : {}),
    ...(characterBatchCompletion ? { character_batch_completion: characterBatchCompletion } : {}),
    ...(storyOverviewAdjustment ? { story_overview_adjustment: storyOverviewAdjustment } : {}),
    ...(eventTimelineAdjustment ? { event_timeline_adjustment: eventTimelineAdjustment } : {}),
  })).data;
}

export async function applyOutlineAgentAction(
  projectId: number,
  jobId: number,
  expectedSourceVersion: number,
  selectedCharacterKeys?: string[],
  selectedOptionIndex?: number,
): Promise<CreationSession> {
  return (await http.post<CreationSession>(
    `/projects/${projectId}/creation-session/agent-actions/${jobId}/apply`,
    { expected_source_version: expectedSourceVersion, ...(selectedCharacterKeys ? { selected_character_keys: selectedCharacterKeys } : {}), ...(selectedOptionIndex !== undefined ? { selected_option_index: selectedOptionIndex } : {}) },
  )).data;
}

export type CreativeDirectionProposal = {
  id: string;
  title: string;
  spine: string;
  relationships: string;
  difference: string;
};

export type CreativeDirectionUnderstanding = {
  genre: string;
  conflict: string;
  characters: string;
  tone: string;
  audience: string;
};

export async function createCreativeDirectionJob(
  projectId: number,
  understanding: Partial<CreativeDirectionUnderstanding>,
): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/creation-session/creative/proposal-jobs`,
    understanding,
  )).data;
}

export async function proposeCreativeDirections(
  projectId: number,
  understanding: { genre?: string; conflict?: string; characters?: string; tone?: string },
): Promise<{ proposals: CreativeDirectionProposal[]; fingerprint: string }> {
  return (await http.post<{ proposals: CreativeDirectionProposal[]; fingerprint: string }>(
    `/projects/${projectId}/creation-session/creative/proposals`,
    understanding,
  )).data;
}

export async function updateCreativeSpecs(
  projectId: number,
  specs: { episode_count: number; episode_duration: number; market: "domestic" | "overseas"; narrative_spec?: NarrativeSpec; expected_narrative_revision?: number },
): Promise<CreationSession> {
  return (await http.patch<CreationSession>(
    `/projects/${projectId}/creation-session/creative/specs`,
    specs,
  )).data;
}

export async function submitCreativeDirection(
  projectId: number,
  selectedOption: string,
  extraRequirements = "",
  proposal?: CreativeDirectionProposal,
  proposalFingerprint?: string,
): Promise<CreationSession> {
  return (await http.post<CreationSession>(
    `/projects/${projectId}/creation-session/creative/direction`,
    proposal
      ? { selected_option: selectedOption, extra_requirements: extraRequirements, proposal, proposal_fingerprint: proposalFingerprint }
      : { selected_option: selectedOption, extra_requirements: extraRequirements },
  )).data;
}

export async function confirmCreativeStory(
  projectId: number,
  action: "confirm" | "adjust" = "confirm",
  adjustment = "",
): Promise<Job | CreationSession> {
  return (await http.post<Job | CreationSession>(
    `/projects/${projectId}/creation-session/creative/confirm`,
    { action, adjustment },
  )).data;
}

export async function runScriptStudy(projectId: number): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/creation-session/script-study`,
  )).data;
}

export async function extractEpisodeOutlineFromFinalScript(projectId: number): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/creation-session/optional-extractions/episode-outline`,
  )).data;
}

export async function extractStoryBibleFromFinalScript(projectId: number): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/creation-session/optional-extractions/story-bible`,
  )).data;
}

export async function runScriptAssetBreakdown(
  projectId: number,
  scope: { requirement_types?: string[]; episode_numbers?: number[] } = {},
): Promise<Job> {
  return (await http.post<Job>(
    `/projects/${projectId}/creation-session/asset-breakdown`,
    scope,
  )).data;
}

export async function confirmScriptAssetBreakdown(
  projectId: number,
): Promise<Record<string, number>> {
  return (await http.post<Record<string, number>>(
    `/projects/${projectId}/creation-session/asset-breakdown/confirm`,
  )).data;
}

export async function updateScriptAssetCandidate(
  projectId: number,
  candidateId: number,
  payload: Partial<Pick<ScriptAssetCandidate, "selected" | "name" | "description" | "prompt_anchor" | "aliases" | "episode_numbers" | "matched_asset_id">>,
): Promise<ScriptAssetCandidate> {
  return (await http.patch<ScriptAssetCandidate>(
    `/projects/${projectId}/creation-session/asset-breakdown/candidates/${candidateId}`,
    payload,
  )).data;
}

export async function mergeScriptAssetCandidate(
  projectId: number,
  candidateId: number,
  targetCandidateId: number,
): Promise<ScriptAssetCandidate> {
  return (await http.post<ScriptAssetCandidate>(
    `/projects/${projectId}/creation-session/asset-breakdown/candidates/${candidateId}/merge`,
    { target_candidate_id: targetCandidateId },
  )).data;
}

export async function rejectScriptAssetBreakdown(
  projectId: number,
): Promise<Record<string, unknown>> {
  return (await http.post<Record<string, unknown>>(
    `/projects/${projectId}/creation-session/asset-breakdown/reject`,
  )).data;
}

export async function restoreCreationArtifact(
  projectId: number,
  artifactId: number,
  artifactType: "story_bible" | "episode_outline",
  baseline?: { expected_current_id: number; expected_revision: number },
): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(
    `/projects/${projectId}/creation-session/artifacts/${artifactId}/restore`,
    { artifact_type: artifactType, ...baseline },
  )).data;
}

export async function confirmStoryBible(
  sessionId: number,
  artifactId: number,
  expectedRevision: number,
): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/confirm`,
    { expected_revision: expectedRevision },
  )).data;
}

export async function recheckScriptAssetBreakdown(projectId: number) {
  const { data } = await http.post<{ blocking_issue_count: number; selected_count: number }>(
    `/projects/${projectId}/creation-session/asset-breakdown/recheck`,
  );
  return data;
}

export async function fillMissingAudioCandidatePrompts(
  projectId: number,
): Promise<{ filled_count: number }> {
  return (await http.post<{ filled_count: number }>(
    `/projects/${projectId}/creation-session/asset-breakdown/fill-audio-prompts`,
  )).data;
}

export async function acknowledgeFormalScriptCandidateReviews(
  projectId: number,
): Promise<{ acknowledged_count: number }> {
  return (await http.post<{ acknowledged_count: number }>(
    `/projects/${projectId}/creation-session/asset-breakdown/acknowledge-formal-sources`,
  )).data;
}

export async function saveStoryBibleVersion(sessionId: number, artifactId: number, content: StoryBibleContent, expectedRevision: number): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(`/creation/sessions/${sessionId}/artifacts/${artifactId}/story-bible/version`, { content, expected_revision: expectedRevision })).data;
}

export async function generateEpisodeOutline(sessionId: number, storyArtifactId: number, storyRevision: number, confirmStory = false): Promise<Job> {
  return (await http.post<Job>(`/creation/sessions/${sessionId}/episode-outline`, {
    expected_story_artifact_id: storyArtifactId,
    expected_story_revision: storyRevision,
    confirm_story: confirmStory,
  })).data;
}

export async function updateEpisodeOutline(
  sessionId: number,
  artifactId: number,
  content: EpisodeOutlineContent,
  expectedRevision?: number,
): Promise<CreationArtifact> {
  return (await http.patch<CreationArtifact>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/episode-outline`,
    expectedRevision === undefined ? { content } : { content, expected_revision: expectedRevision },
  )).data;
}

export async function saveEpisodeOutlineVersion(
  sessionId: number,
  artifactId: number,
  content: EpisodeOutlineContent,
  expectedRevision?: number,
): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/episode-outline/version`,
    { content, expected_revision: expectedRevision },
  )).data;
}

export async function getOutlineManagement(sessionId: number, artifactId: number): Promise<CreationArtifact> {
  return (await http.get<CreationArtifact>(`/creation/sessions/${sessionId}/artifacts/${artifactId}/episode-outline/management`)).data;
}

export async function operateOutline(sessionId: number, artifactId: number, operation: {
  action: "add" | "delete" | "restore" | "purge" | "move" | "duplicate";
  expected_revision: number;
  request_id: string;
  outline_key?: string;
  position?: number;
  title?: string;
  duration_seconds?: number;
  count?: number;
}): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(`/creation/sessions/${sessionId}/artifacts/${artifactId}/episode-outline/operations`, operation)).data;
}

export async function confirmEpisodeOutline(
  sessionId: number,
  artifactId: number,
  payload?: { content: EpisodeOutlineContent; expected_revision: number },
): Promise<CreationArtifact> {
  return (await http.post<CreationArtifact>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/confirm-outline`,
    payload ?? {},
  )).data;
}

export async function generateSceneShotDraft(sessionId: number): Promise<Job> {
  return (await http.post<Job>(`/creation/sessions/${sessionId}/scene-shot-draft`)).data;
}

export async function updateSceneShotDraft(
  sessionId: number,
  artifactId: number,
  content: SceneShotDraftContent,
): Promise<CreationArtifact> {
  return (await http.patch<CreationArtifact>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/scene-shot-draft`,
    { content },
  )).data;
}

export async function publishSceneShotDraft(
  sessionId: number,
  artifactId: number,
): Promise<SceneShotPublishResult> {
  return (await http.post<SceneShotPublishResult>(
    `/creation/sessions/${sessionId}/artifacts/${artifactId}/publish-scene-shots`,
  )).data;
}
