/** Episode production, segment planning, export, and director APIs. */

import { http } from "@/api/client";
import type { Episode, EpisodeDialogueCueList, EpisodeEngineeringPackagePreflight, EpisodeEngineeringPackageVersion, EpisodeExportPreflight, EpisodeExportVersion, EpisodeJianyingDraftPreflight, EpisodeJianyingDraftVersion, EpisodePremiereXmlPreflight, EpisodePremiereXmlVersion, EpisodeProduction, EpisodeProductionPlan, EpisodeProductionSettings, Job, ProjectScriptReadiness, SegmentContinuityReport, SegmentFirstFramePlan, SegmentLifecycleInput, SegmentPlanAdjustInput, SegmentProductionPlan, SegmentProductionPlanInput, SegmentVideoVersion } from "@/types/api";

export async function listEpisodes(projectId: number): Promise<Episode[]> {
  const { data } = await http.get<Episode[]>(`/projects/${projectId}/episodes`);
  return data;
}

export async function getProjectScriptReadiness(projectId: number): Promise<ProjectScriptReadiness> {
  const { data } = await http.get<ProjectScriptReadiness>(`/projects/${projectId}/script-readiness`);
  return data;
}

export async function finalizeProjectScripts(
  projectId: number,
  episodes: Array<{ episode_id: number; expected_script_revision: number; duration_estimate: number }>,
): Promise<ProjectScriptReadiness> {
  const { data } = await http.post<ProjectScriptReadiness>(`/projects/${projectId}/script-finalization`, {
    confirmed: true,
    episodes,
  });
  return data;
}

export async function listEpisodeProductions(projectId: number): Promise<EpisodeProduction[]> {
  const { data } = await http.get<EpisodeProduction[]>(`/projects/${projectId}/episode-productions`);
  return data;
}

export async function getEpisodeProduction(projectId: number, episodeId: number): Promise<EpisodeProduction> {
  const { data } = await http.get<EpisodeProduction>(`/projects/${projectId}/episodes/${episodeId}/production`);
  return data;
}

export async function getEpisodeDialogueCues(
  projectId: number,
  episodeId: number,
): Promise<EpisodeDialogueCueList> {
  const { data } = await http.get<EpisodeDialogueCueList>(
    `/projects/${projectId}/episodes/${episodeId}/production/dialogue-cues`,
  );
  return data;
}

export async function updateEpisodeProduction(
  projectId: number,
  episodeId: number,
  settings: EpisodeProductionSettings,
  expectedRevision: number,
): Promise<EpisodeProduction> {
  const { data } = await http.patch<EpisodeProduction>(`/projects/${projectId}/episodes/${episodeId}/production`, {
    settings,
    expected_revision: expectedRevision,
  });
  return data;
}

export async function getSegmentProductionPlan(
  projectId: number,
  episodeId: number,
): Promise<SegmentProductionPlan> {
  const { data } = await http.get<SegmentProductionPlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-plan`,
  );
  return data;
}

export async function createSegmentProductionPlan(
  projectId: number,
  episodeId: number,
  payload: SegmentProductionPlanInput,
): Promise<SegmentProductionPlan> {
  const { data } = await http.post<SegmentProductionPlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-plan`,
    payload,
  );
  return data;
}

export async function adjustSegmentProductionPlan(
  projectId: number,
  episodeId: number,
  payload: SegmentPlanAdjustInput,
): Promise<SegmentProductionPlan> {
  const { data } = await http.post<SegmentProductionPlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-plan/adjust`,
    payload,
  );
  return data;
}

export async function changeSegmentLifecycle(
  projectId: number,
  episodeId: number,
  payload: SegmentLifecycleInput,
): Promise<SegmentProductionPlan> {
  const { data } = await http.post<SegmentProductionPlan>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/segment-plan/lifecycle",
    payload,
  );
  return data;
}

export async function checkSegmentPlanContinuity(
  projectId: number,
  episodeId: number,
): Promise<SegmentContinuityReport> {
  const { data } = await http.post<SegmentContinuityReport>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-plan/continuity-check`,
  );
  return data;
}

export interface EpisodeProductionPlanInput {
  provider_model_id: number;
  parameters?: Record<string, unknown>;
  regenerate?: boolean;
}

export async function planEpisodeProduction(
  projectId: number,
  episodeId: number,
  payload: EpisodeProductionPlanInput,
): Promise<EpisodeProductionPlan> {
  const { data } = await http.post<EpisodeProductionPlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/plan`,
    payload,
  );
  return data;
}

export async function startEpisodeProduction(
  projectId: number,
  episodeId: number,
  payload: EpisodeProductionPlanInput & { request_id: string; confirmed: true; max_cost_cents: number; expected_plan_id?: number; expected_plan_revision?: number },
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/production/start`,
    payload,
  );
  return data;
}

export async function planSegmentVideoAttempt(
  projectId: number,
  episodeId: number,
  segmentId: number,
  payload: EpisodeProductionPlanInput,
): Promise<EpisodeProductionPlan> {
  const { data } = await http.post<EpisodeProductionPlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/segments/${segmentId}/video/plan`,
    payload,
  );
  return data;
}

export async function startSegmentVideoAttempt(
  projectId: number,
  episodeId: number,
  segmentId: number,
  payload: EpisodeProductionPlanInput & {
    request_id: string;
    confirmed: true;
    max_cost_cents: number;
    expected_plan_id: number;
    expected_plan_revision: number;
    expected_video_prompt_fingerprint?: string;
  },
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/production/segments/${segmentId}/video/start`,
    payload,
  );
  return data;
}

export async function startSegmentFirstFrames(
  projectId: number,
  episodeId: number,
  payload: {
    segment_ids: number[];
    provider_model_id: number;
    parameters?: Record<string, unknown>;
    negative_prompt?: string | null;
    request_id: string;
    confirmed: true;
    max_cost_cents: number;
    expected_plan_id: number;
    expected_plan_revision: number;
  },
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-first-frames`,
    payload,
  );
  return data;
}

export async function planSegmentFirstFrames(
  projectId: number,
  episodeId: number,
  payload: {
    segment_ids: number[];
    provider_model_id: number;
    parameters?: Record<string, unknown>;
    negative_prompt?: string | null;
  },
): Promise<SegmentFirstFramePlan> {
  const { data } = await http.post<SegmentFirstFramePlan>(
    `/projects/${projectId}/episodes/${episodeId}/production/segment-first-frames/plan`,
    payload,
  );
  return data;
}

export async function selectSegmentVideoVersion(
  projectId: number,
  episodeId: number,
  segmentId: number,
  versionId: number,
  expectedPlanRevision: number,
  expectedInputFingerprint: string,
): Promise<SegmentVideoVersion> {
  const { data } = await http.post<SegmentVideoVersion>(
    `/projects/${projectId}/episodes/${episodeId}/production/segments/${segmentId}/video-versions/${versionId}/select`,
    {
      expected_plan_revision: expectedPlanRevision,
      expected_input_fingerprint: expectedInputFingerprint,
    },
  );
  return data;
}

export async function startEpisodeExport(
  projectId: number,
  episodeId: number,
  requestId: string,
  expectedSnapshotFingerprint: string,
): Promise<Job> {
  const { data } = await http.post<Job>(
    `/projects/${projectId}/episodes/${episodeId}/production/export`,
    { request_id: requestId, confirmed: true, expected_snapshot_fingerprint: expectedSnapshotFingerprint },
  );
  return data;
}

export async function getEpisodeExportPreflight(
  projectId: number,
  episodeId: number,
): Promise<EpisodeExportPreflight> {
  const { data } = await http.get<EpisodeExportPreflight>(
    `/projects/${projectId}/episodes/${episodeId}/production/export-preflight`,
  );
  return data;
}

export async function listEpisodeExportVersions(
  projectId: number,
  episodeId: number,
): Promise<EpisodeExportVersion[]> {
  const { data } = await http.get<EpisodeExportVersion[]>(
    `/projects/${projectId}/episodes/${episodeId}/production/exports`,
  );
  return data;
}

export async function getEpisodeEngineeringPackagePreflight(
  projectId: number,
  episodeId: number,
): Promise<EpisodeEngineeringPackagePreflight> {
  return (await http.get<EpisodeEngineeringPackagePreflight>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/engineering-package-preflight",
  )).data;
}

export async function startEpisodeEngineeringPackage(
  projectId: number,
  episodeId: number,
  requestId: string,
  expectedPackageFingerprint: string,
): Promise<Job> {
  return (await http.post<Job>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/engineering-packages",
    { request_id: requestId, confirmed: true, expected_package_fingerprint: expectedPackageFingerprint },
  )).data;
}

export async function listEpisodeEngineeringPackages(
  projectId: number,
  episodeId: number,
): Promise<EpisodeEngineeringPackageVersion[]> {
  return (await http.get<EpisodeEngineeringPackageVersion[]>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/engineering-packages",
  )).data;
}

export async function getEpisodePremiereXmlPreflight(
  projectId: number,
  episodeId: number,
): Promise<EpisodePremiereXmlPreflight> {
  return (await http.get<EpisodePremiereXmlPreflight>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/premiere-xml-preflight",
  )).data;
}

export async function startEpisodePremiereXml(
  projectId: number,
  episodeId: number,
  requestId: string,
  expectedPackageFingerprint: string,
): Promise<Job> {
  return (await http.post<Job>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/premiere-xml-packages",
    { request_id: requestId, confirmed: true, expected_package_fingerprint: expectedPackageFingerprint },
  )).data;
}

export async function listEpisodePremiereXmlPackages(
  projectId: number,
  episodeId: number,
): Promise<EpisodePremiereXmlVersion[]> {
  return (await http.get<EpisodePremiereXmlVersion[]>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/premiere-xml-packages",
  )).data;
}

export async function getEpisodeJianyingDraftPreflight(
  projectId: number,
  episodeId: number,
): Promise<EpisodeJianyingDraftPreflight> {
  return (await http.get<EpisodeJianyingDraftPreflight>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/jianying-draft-preflight",
  )).data;
}

export async function startEpisodeJianyingDraft(
  projectId: number,
  episodeId: number,
  requestId: string,
  expectedPackageFingerprint: string,
): Promise<Job> {
  return (await http.post<Job>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/jianying-draft-packages",
    { request_id: requestId, confirmed: true, expected_package_fingerprint: expectedPackageFingerprint },
  )).data;
}

export async function listEpisodeJianyingDraftPackages(
  projectId: number,
  episodeId: number,
): Promise<EpisodeJianyingDraftVersion[]> {
  return (await http.get<EpisodeJianyingDraftVersion[]>(
    "/projects/" + projectId + "/episodes/" + episodeId + "/production/jianying-draft-packages",
  )).data;
}
