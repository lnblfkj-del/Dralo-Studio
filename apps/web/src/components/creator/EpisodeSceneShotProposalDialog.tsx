import { Plus, Trash2 } from "lucide-react";

import type { SceneDraft, SceneShotDraftContent, ShotDraft } from "@/types/api";
import { Button, Dialog } from "@/components/ui";

function blankShot(number: number): ShotDraft {
  return {
    number,
    duration: 4,
    shot_size: "中景",
    camera_angle: "平视",
    camera_movement: "固定",
    action: "待完善分镜动作",
    dialogue: "",
    audio_note: "",
    characters: [],
    asset_ids: [],
  };
}

function renumber(scenes: SceneDraft[]): SceneDraft[] {
  return scenes.map((scene, sceneIndex) => ({
    ...scene,
    number: sceneIndex + 1,
    shots: scene.shots.map((shot, shotIndex) => ({ ...shot, number: shotIndex + 1 })),
  }));
}

export function EpisodeSceneShotProposalDialog({
  draft,
  assetNames,
  busy,
  error,
  onChange,
  onApply,
  onReject,
}: {
  draft: SceneShotDraftContent;
  assetNames: Record<number, string>;
  busy: boolean;
  error?: string;
  onChange: (draft: SceneShotDraftContent) => void;
  onApply: () => void;
  onReject: () => void;
}) {
  const shotCount = draft.scenes.reduce((total, scene) => total + scene.shots.length, 0);
  const updateScene = (sceneIndex: number, patch: Partial<SceneDraft>) => onChange({
    ...draft,
    scenes: draft.scenes.map((scene, index) => index === sceneIndex ? { ...scene, ...patch } : scene),
  });
  const updateShot = (sceneIndex: number, shotIndex: number, patch: Partial<ShotDraft>) => onChange({
    ...draft,
    scenes: draft.scenes.map((scene, index) => index === sceneIndex ? {
      ...scene,
      shots: scene.shots.map((shot, innerIndex) => innerIndex === shotIndex ? { ...shot, ...patch } : shot),
    } : scene),
  });
  const removeScene = (sceneIndex: number) => onChange({
    ...draft,
    scenes: renumber(draft.scenes.filter((_, index) => index !== sceneIndex)),
  });
  const addScene = () => onChange({
    ...draft,
    scenes: [...draft.scenes, {
      number: draft.scenes.length + 1,
      name: `场景 ${draft.scenes.length + 1}`,
      location: "待完善地点",
      time_of_day: "",
      description: "",
      shots: [blankShot(1)],
    }],
  });
  const removeShot = (sceneIndex: number, shotIndex: number) => updateScene(sceneIndex, {
    shots: draft.scenes[sceneIndex]!.shots
      .filter((_, index) => index !== shotIndex)
      .map((shot, index) => ({ ...shot, number: index + 1 })),
  });
  const addShot = (sceneIndex: number) => updateScene(sceneIndex, {
    shots: [...draft.scenes[sceneIndex]!.shots, blankShot(draft.scenes[sceneIndex]!.shots.length + 1)],
  });

  return <Dialog open className="episode-proposal-modal" title="场景与分镜拆解提案" description={`${draft.scenes.length} 场 · ${shotCount} 镜，确认前不会写入正式制作结构。`} size="large" busy={busy} onClose={onReject} footer={<><span className="episode-proposal-footer-note">应用时会校验剧本版本、项目资产和现有场景，避免覆盖并发修改。</span><Button disabled={busy} onClick={onReject}>放弃提案</Button><Button variant="primary" disabled={busy} onClick={onApply}>确认写入场景分镜</Button></>}>
    <div className="episode-proposal-dialog">
      <div className="episode-proposal-scenes">
        {draft.scenes.map((scene, sceneIndex) => <article className="episode-proposal-scene" key={scene.number}>
          <header><strong>场景 {String(scene.number).padStart(2, "0")}</strong>{draft.scenes.length > 1 && <button aria-label={`删除场景 ${scene.number}`} disabled={busy} onClick={() => removeScene(sceneIndex)}><Trash2 size={14} /></button>}</header>
          <div className="episode-proposal-scene-fields">
            <label><span>场景名称</span><input disabled={busy} value={scene.name} onChange={(event) => updateScene(sceneIndex, { name: event.target.value })} /></label>
            <label><span>地点</span><input disabled={busy} value={scene.location} onChange={(event) => updateScene(sceneIndex, { location: event.target.value })} /></label>
            <label><span>时间</span><input disabled={busy} value={scene.time_of_day} onChange={(event) => updateScene(sceneIndex, { time_of_day: event.target.value })} /></label>
            <label className="wide"><span>场景描述</span><input disabled={busy} value={scene.description} onChange={(event) => updateScene(sceneIndex, { description: event.target.value })} /></label>
          </div>
          <div className="episode-proposal-shots">
            {scene.shots.map((shot, shotIndex) => <section key={shot.number}>
              <header><b>分镜 {scene.number}.{shot.number}</b>{scene.shots.length > 1 && <button aria-label={`删除分镜 ${scene.number}.${shot.number}`} disabled={busy} onClick={() => removeShot(sceneIndex, shotIndex)}><Trash2 size={13} /></button>}</header>
              <div>
                <label><span>时长</span><input type="number" min="0.1" max="120" step="0.1" disabled={busy} value={shot.duration} onChange={(event) => updateShot(sceneIndex, shotIndex, { duration: Number(event.target.value) })} /></label>
                <label><span>景别</span><input disabled={busy} value={shot.shot_size} onChange={(event) => updateShot(sceneIndex, shotIndex, { shot_size: event.target.value })} /></label>
                <label><span>机位</span><input disabled={busy} value={shot.camera_angle} onChange={(event) => updateShot(sceneIndex, shotIndex, { camera_angle: event.target.value })} /></label>
                <label><span>运镜</span><input disabled={busy} value={shot.camera_movement} onChange={(event) => updateShot(sceneIndex, shotIndex, { camera_movement: event.target.value })} /></label>
                <label className="wide"><span>动作</span><textarea disabled={busy} value={shot.action} onChange={(event) => updateShot(sceneIndex, shotIndex, { action: event.target.value })} /></label>
                <label><span>对白</span><textarea disabled={busy} value={shot.dialogue} onChange={(event) => updateShot(sceneIndex, shotIndex, { dialogue: event.target.value })} /></label>
                <label><span>声音</span><textarea disabled={busy} value={shot.audio_note} onChange={(event) => updateShot(sceneIndex, shotIndex, { audio_note: event.target.value })} /></label>
              </div>
              {(shot.asset_ids?.length ?? 0) > 0 && <p className="episode-proposal-assets">引用资产：{shot.asset_ids!.map((id) => assetNames[id] ?? `#${id}`).join("、")}</p>}
            </section>)}
          </div>
          <button className="episode-proposal-add" disabled={busy} onClick={() => addShot(sceneIndex)}><Plus size={14} />添加分镜</button>
        </article>)}
        <button className="episode-proposal-add scene" disabled={busy} onClick={addScene}><Plus size={15} />添加场景</button>
      </div>
      {error && <p className="studio-error" role="alert">{error}</p>}
    </div>
  </Dialog>;
}
