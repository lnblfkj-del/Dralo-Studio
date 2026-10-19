import type { PlanningMode } from "@/api/planningCapabilities";
import type { VideoConfigInput } from "@/types/provider";

export const VIDEO_SPEC_KEYS = ["episode_planning_capability", "durations", "resolutions", "aspect_ratios",
  "max_reference_images", "supported_video_input_modes", "supports_first_frame", "supports_last_frame",
  "supports_audio", "supports_dialogue", "supports_negative_prompt", "max_shots_per_segment",
  "native_audio_capability", "_audio_verification"];
const DEFAULT_KEYS = new Set(["duration", "resolution", "aspect_ratio", "generate_audio", "audio", "seed"]);

export function videoConfigInput(options: {modes: PlanningMode[]; params: Record<string, unknown>; shots: string;
  base: VideoConfigInput | null}): VideoConfigInput | undefined {
  if (!options.modes.length) return undefined;
  const shots = options.shots.trim() ? Number(options.shots) : null;
  if (shots != null && (!Number.isInteger(shots) || shots < 1 || shots > 20)) throw new Error("每段建议镜头数须为1到20");
  const params = Object.fromEntries(Object.entries(options.params).filter(([key]) => !VIDEO_SPEC_KEYS.includes(key)));
  const planning_policy = {...options.base?.planning_policy};
  if (shots == null) delete planning_policy.max_shots_per_segment;
  else planning_policy.max_shots_per_segment = shots;
  const isDefault = (key: string) => DEFAULT_KEYS.has(key) || Object.hasOwn(options.base?.generation_defaults ?? {}, key);
  return {
    ...(options.base?.integration !== undefined ? {integration: options.base.integration} : {}),
    schema_version: "video-model.v1", specification: {...options.base?.specification, modes: options.modes},
    generation_defaults: Object.fromEntries(Object.entries(params).filter(([key]) => isDefault(key))),
    transport_params: Object.fromEntries(Object.entries(params).filter(([key]) => !isDefault(key))),
    planning_policy,
  };
}
