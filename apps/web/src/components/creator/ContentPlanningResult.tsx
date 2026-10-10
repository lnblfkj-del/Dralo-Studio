import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Clapperboard, RefreshCw, Save, WandSparkles } from "lucide-react";
import { toErrorMessage } from "@/api/client";
import { getJob } from "@/api/jobs";
import { activateContentProduction, checkContentModel, getContentCapability, getContentResult, saveContentProductionDraft, switchContentModel, type ContentRunScope, type ModelCheck } from "@/api/contentPlanning";
import { Button } from "@/components/ui";
import type { Job, ModelOption } from "@/types/api";
import { ContentSegmentOptimization } from "./ContentSegmentOptimization";
import "./ContentPlanningResult.css";

const issueLabels: Record<string, string> = {
  duration_requires_replan: "片段时长超出模型规格", mode_parameters_changed: "画幅、清晰度或输入模式变化",
  shot_count_requires_replan: "镜头数量超出模型规格",
  mode_unavailable: "当前模式不可用", reference_constraints: "参考素材数量或类型不符合模型要求",
  native_dialogue_unavailable: "模型不支持所需的原生对白", native_audio_unavailable: "模型不支持所需的原生声音",
  background_music_unavailable: "模型不支持所选配乐要求",
};
const inputLabels: Record<string, string> = { text: "纯文本", single_image: "单图", multi_reference: "多参考图", first_frame: "首帧", first_last_frame: "首尾帧" };

export function ContentPlanningResult({ scope, videoModels, disabled = false, onChanged, onBusyChange }: {
  scope: ContentRunScope; videoModels: ModelOption[]; disabled?: boolean; onChanged?: (job: Job) => void; onBusyChange?: (busy: boolean) => void;
}) {
  const client = useQueryClient();
  const [target, setTarget] = useState<number | null>(null);
  const [mode, setMode] = useState("");
  const [checked, setChecked] = useState<{ result: ModelCheck; requestId: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<number | null>(null);
  const [optimization, setOptimization] = useState<{ key: string; index: number } | null>(null);
  const locked = busy || Boolean(optimization);
  const inFlight = useRef(false);
  useEffect(() => { onBusyChange?.(locked); }, [locked, onBusyChange]);
  useEffect(() => () => onBusyChange?.(false), [onBusyChange]);
  const result = useQuery({ queryKey: ["content-plan", scope.projectId, scope.episodeId, scope.jobId], queryFn: () => getContentResult(scope), retry: false });
  const capability = useQuery({ queryKey: ["content-capability", scope.projectId, scope.episodeId, target], queryFn: () => getContentCapability(scope, target!), enabled: target !== null, retry: false });
  const assembly = result.data?.assembly;
  const production = result.data?.production;
  const draftVersion = saved ?? production?.projected_version;
  const selectedMode = capability.data?.modes.find(item => item.key === mode)?.key ?? "";
  async function act(action: () => Promise<void>) {
    if (inFlight.current || disabled || optimization) return;
    inFlight.current = true; setBusy(true); setError("");
    try { await action(); } catch (failure) { setError(toErrorMessage(failure)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <div className="content-plan-result">
    {assembly && <>
      <div className="content-plan-summary"><strong>{assembly.segments.length} 个片段 · {assembly.timeline_ms / 1000} 秒</strong>
        <Button icon={draftVersion ? <Check size={16} /> : <Save size={16} />} disabled={disabled || locked || Boolean(draftVersion)} onClick={() => void act(async () => {
          const value = await saveContentProductionDraft(scope, assembly); setSaved(value.version);
        })}>{production?.active ? `制作计划 V${draftVersion}` : draftVersion ? `已保存草稿 V${draftVersion}` : "保存生产草稿"}</Button>
        {production && <Button variant="primary" icon={production.active ? <Check size={16} /> : <Clapperboard size={16} />} disabled={disabled || locked || production.active} onClick={() => void act(async () => {
          await activateContentProduction(scope, assembly, production);
          await Promise.all([
            client.invalidateQueries({ queryKey: ["content-plan", scope.projectId, scope.episodeId, scope.jobId] }),
            client.invalidateQueries({ queryKey: ["segment-production-plan", scope.projectId, scope.episodeId] }),
            client.invalidateQueries({ queryKey: ["episode-production", scope.projectId, scope.episodeId] }),
            client.invalidateQueries({ queryKey: ["episode-studio", scope.projectId, scope.episodeId] }),
            client.invalidateQueries({ queryKey: ["episode-productions", scope.projectId] }),
          ]);
        })}>{production.active ? "已启用到制作台" : "启用到制作台"}</Button>}
      </div>
      <ol className="content-plan-segments">{assembly.segments.map((segment, index) => <li key={segment.key}>
        <details><summary>片段 {index + 1} · {segment.timeline_start_ms / 1000}–{segment.timeline_end_ms / 1000} 秒 · {segment.shots.length} 个镜头</summary>
          {onChanged && <div className="content-plan-segment-actions"><Button variant="text" icon={<WandSparkles size={16} />} disabled={disabled || locked || result.data?.status !== "succeeded"}
            aria-label={`优化片段 ${index + 1}`} onClick={() => setOptimization({ key: segment.key, index })}>优化此片段</Button></div>}
          {segment.shots.map(shot => <div className="content-plan-shot" key={shot.key}>
            <small>{shot.start_ms / 1000}–{shot.end_ms / 1000} 秒 · {shot.direction.shot_size} · {shot.direction.camera_angle} · {shot.direction.camera_movement}</small>
            {shot.sources.map(source => <p key={source.key}>{source.text}</p>)}<p>{shot.direction.action}</p>
          </div>)}
        </details>
      </li>)}</ol>
    </>}
    {(assembly || result.data?.has_frozen_plan) && <fieldset className="content-plan-switch" disabled={disabled || locked}>
      <legend>切换视频模型</legend>
      <label>目标模型<select aria-label="切换目标视频模型" value={target ?? ""} onChange={event => { setTarget(Number(event.target.value) || null); setMode(""); setChecked(null); setError(""); }}>
        <option value="">选择模型</option>{videoModels.map(model => <option key={model.id} value={model.id}>{model.provider_name} · {model.name}</option>)}
      </select></label>
      <label>模式<select aria-label="切换目标模式" value={selectedMode} onChange={event => { setMode(event.target.value); setChecked(null); }}>
        <option value="">选择模式</option>{capability.data?.modes.map(item => <option key={item.key} value={item.key}>{inputLabels[item.input_mode] ?? item.input_mode} · {item.aspect_ratio} · {item.resolution}</option>)}
      </select></label>
      <Button icon={<RefreshCw size={16} />} disabled={!target || !selectedMode || disabled || locked} onClick={() => void act(async () => {
        const value = await checkContentModel(scope, target!, selectedMode);
        setChecked({ result: value, requestId: crypto.randomUUID() });
      })}>检查兼容性</Button>
    </fieldset>}
    {checked && <div className="content-plan-switch-result">
      <p>{checked.result.compatible ? "规格兼容，将复用已完成的片段脚本。" : "规格不兼容，需要重新规划受影响的片段。"}</p>
      {!checked.result.compatible && <ul>{[...new Set(checked.result.issues.map(issue => issueLabels[issue.code] ?? "模型能力不满足当前片段要求"))].map(label => <li key={label}>{label}</li>)}</ul>}
      <p>本次切换不调用模型、不生成视频；需要补写脚本时另行确认费用。</p>
      <Button disabled={disabled || locked} onClick={() => void act(async () => {
        const value = await switchContentModel(scope, target!, selectedMode, checked.result, checked.requestId);
        // Keep the key until mutation and follow-up read both succeed.
        const job = await getJob(value.job_id);
        await client.invalidateQueries({ queryKey: ["content-plan"] });
        onChanged?.(job); setChecked(null);
      })}>{checked.result.compatible ? "确认切换" : "按新模型重新规划"}</Button>
    </div>}
    {busy && <p role="status">正在处理…</p>}
    {(error || result.error || capability.error) && <p role="alert">{error || toErrorMessage(result.error || capability.error)}</p>}
    {optimization && assembly && onChanged && <ContentSegmentOptimization key={`${scope.jobId}:${optimization.key}`} scope={scope} assembly={assembly}
      segmentKey={optimization.key} index={optimization.index} onChanged={onChanged} onClose={() => setOptimization(null)} />}
  </div>;
}
