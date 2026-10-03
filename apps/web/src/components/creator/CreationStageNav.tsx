/* eslint-disable react-refresh/only-export-components -- stage contract is shared with route restoration tests. */
import { Tooltip } from "@/components/ui";
export type CreationStage = "story" | "outline" | "script" | "prep";

const STAGES_ORDER: CreationStage[] = ["story", "outline", "script", "prep"];

export function creationStageStorageKey(projectId: number) {
  return `creation-stage:${projectId}`;
}

export function readStoredCreationStage(projectId: number): CreationStage | null {
  try {
    const value = window.localStorage.getItem(creationStageStorageKey(projectId));
    return STAGES_ORDER.includes(value as CreationStage) ? value as CreationStage : null;
  } catch {
    return null;
  }
}

export function writeStoredCreationStage(projectId: number, stage: CreationStage) {
  try {
    window.localStorage.setItem(creationStageStorageKey(projectId), stage);
  } catch {
    /* ignore quota / private mode */
  }
}

export function restoreCreationStage({
  requested,
  stored,
  hasStory,
  hasOutline,
  outlineLocked,
  hasScripts = false,
  scriptsConfirmed,
}: {
  requested?: string | null;
  stored?: CreationStage | null;
  hasStory: boolean;
  hasOutline: boolean;
  outlineLocked: boolean;
  hasScripts?: boolean;
  scriptsConfirmed: boolean;
}): CreationStage {
  const availability: Record<CreationStage, boolean> = {
    story: true,
    outline: hasStory,
    script: outlineLocked || hasScripts,
    prep: scriptsConfirmed,
  };
  const pick = STAGES_ORDER.includes(requested as CreationStage) ? requested as CreationStage : stored;
  if (pick && availability[pick]) return pick;
  if (scriptsConfirmed) return "prep";
  if (outlineLocked || hasScripts) return "script";
  if (hasOutline || hasStory) return "outline";
  return "story";
}

export type StageAvailability = Record<CreationStage, {
  ready: boolean;
  completed: boolean;
  activity?: "running" | "failed";
  skipped?: boolean;
  reason?: string;
  detail?: string;
}>;

const STAGES: Array<{ id: CreationStage; label: string }> = [
  { id: "story", label: "故事策划" },
  { id: "outline", label: "分集大纲" },
  { id: "script", label: "剧本正文" },
  { id: "prep", label: "制作准备" },
];

/** 环节的中文名。Agent 面板头要显示“当前环节”，不能另写一份文案。 */
export function creationStageLabel(stage: CreationStage): string {
  return STAGES.find((item) => item.id === stage)?.label ?? stage;
}

export function CreationStageNav({
  active,
  availability,
  onSelect,
}: {
  active: CreationStage;
  availability: StageAvailability;
  onSelect: (stage: CreationStage) => void;
}) {
  return (
    <nav className="creation-stage-nav" aria-label="剧本创作阶段">
      {STAGES.map((stage) => {
        const state = availability[stage.id];
        const canOpen = !state.skipped && (state.ready || state.completed || Boolean(state.activity));
        const status = state.activity === "running"
          ? "生成中"
          : state.activity === "failed"
            ? "生成失败"
            : state.skipped
              ? "可选"
              : state.completed
                ? "已完成"
                : active === stage.id
                  ? "当前查看"
                  : state.ready
                    ? "可开始"
                    : "未开始";
        return (
          <Tooltip key={stage.id} placement="right" content={`${stage.label} · ${status}${state.detail || state.reason ? `：${state.detail || state.reason}` : ""}`}>
          <button
            type="button"
            aria-label={`${stage.label} · ${status}`}
            className={`${active === stage.id ? "active" : ""} ${state.completed ? "done" : ""} ${state.skipped ? "skipped" : ""} ${state.activity ? `is-${state.activity}` : ""}`}
            aria-current={active === stage.id ? "step" : undefined}
            aria-disabled={!canOpen}
            onClick={() => {
              if (canOpen) onSelect(stage.id);
            }}
          >
            <span className="creation-stage-marker" aria-hidden="true" />
            <span className="creation-stage-short" aria-hidden="true">{{ story: "策划", outline: "大纲", script: "正文", prep: "准备" }[stage.id]}</span>
            <span className="creation-stage-copy"><strong>{stage.label}</strong><small>{state.detail || state.reason || status}</small></span>
            <em>{status}</em>
          </button>
          </Tooltip>
        );
      })}
    </nav>
  );
}
