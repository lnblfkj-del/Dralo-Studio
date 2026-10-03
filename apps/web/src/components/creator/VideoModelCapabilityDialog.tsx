import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { toErrorMessage } from "@/api/client";
import { updateProviderModel } from "@/api/providers";
import { Button, Dialog } from "@/components/ui";
import { formatResolution, videoModelDefaultResolution, videoModelDurations, videoModelResolutions } from "@/domain/videoModelCapabilities";
import { useAuthStore } from "@/stores/authStore";
import type { ModelOption, ProviderModel } from "@/types/api";

interface Props {
  model: ModelOption | null;
  onClose: () => void;
  onSaved: (model: ProviderModel) => void;
}

const durationPresets = [4, 5, 6, 8, 10, 15];
const resolutionPresets = ["480p", "540p", "720p", "1024p", "1080p", "4k"];

function parseDurations(value: string) {
  return [...new Set(value.split(/[，,\s]+/).map(Number).filter((item) => Number.isFinite(item) && item > 0 && item <= 30))].sort((a, b) => a - b);
}

function parseResolutions(value: string) {
  return [...new Set(value.split(/[，,\s]+/).map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

export function VideoModelCapabilityDialog({ model, onClose, onSaved }: Props) {
  const client = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const canManage = user?.role === "admin" || Boolean(user?.permissions?.["providers.edit"]);
  const [durationsText, setDurationsText] = useState("");
  const [resolutionsText, setResolutionsText] = useState("");
  const [defaultResolution, setDefaultResolution] = useState("");

  useEffect(() => {
    setDurationsText(videoModelDurations(model).join(", "));
    setResolutionsText(videoModelResolutions(model).join(", "));
    setDefaultResolution(videoModelDefaultResolution(model));
  }, [model]);

  const durations = useMemo(() => parseDurations(durationsText), [durationsText]);
  const resolutions = useMemo(() => parseResolutions(resolutionsText), [resolutionsText]);
  const valid = durations.length > 0 && resolutions.length > 0;
  const save = useMutation({
    mutationFn: async () => {
      if (!model || !valid) throw new Error("至少配置一个支持时长和一个清晰度");
      const resolution = resolutions.includes(defaultResolution) ? defaultResolution : resolutions[0];
      return updateProviderModel(model.provider_id, model.id, {
        default_params: {
          ...(model.default_params ?? {}),
          durations,
          resolutions,
          resolution,
        },
      });
    },
    onSuccess: async (saved) => {
      await client.invalidateQueries({ queryKey: ["ai-settings"] });
      onSaved(saved);
    },
  });
  const toggleDuration = (value: number) => {
    const next = durations.includes(value) ? durations.filter((item) => item !== value) : [...durations, value].sort((a, b) => a - b);
    setDurationsText(next.join(", "));
  };
  const toggleResolution = (value: string) => {
    const next = resolutions.includes(value) ? resolutions.filter((item) => item !== value) : [...resolutions, value];
    setResolutionsText(next.join(", "));
    if (!next.includes(defaultResolution)) setDefaultResolution(next[0] ?? "");
  };

  return <Dialog
    open={Boolean(model)}
    className="video-capability-dialog"
    title="视频模型能力"
    description={model ? `${model.provider_name} · ${model.name}` : undefined}
    busy={save.isPending}
    dirty={Boolean(model) && (durationsText !== videoModelDurations(model).join(", ") || resolutionsText !== videoModelResolutions(model).join(", ") || defaultResolution !== videoModelDefaultResolution(model))}
    onClose={onClose}
    footer={(requestClose) => <><span className="video-capability-dialog__note">保存后同步更新模型设置与当前工作台。</span><Button disabled={save.isPending} onClick={requestClose}>取消</Button><Button variant="primary" loading={save.isPending} disabled={!canManage || !valid} onClick={() => save.mutate()}>保存模型能力</Button></>}
  >
    <div className="video-capability-form">
      <section>
        <div><strong>支持时长</strong><span>单位为秒，用于片段规划和生成校验。</span></div>
        <div className="video-capability-presets">{durationPresets.map((value) => <button type="button" className={durations.includes(value) ? "selected" : ""} key={value} onClick={() => toggleDuration(value)}>{value}s</button>)}</div>
        <label><span>自定义时长</span><input aria-label="支持时长" value={durationsText} onChange={(event) => setDurationsText(event.target.value)} placeholder="例如：5, 10" /></label>
      </section>
      <section>
        <div><strong>支持清晰度</strong><span>工作台只展示这里启用的选项。</span></div>
        <div className="video-capability-presets">{resolutionPresets.map((value) => <button type="button" className={resolutions.includes(value) ? "selected" : ""} key={value} onClick={() => toggleResolution(value)}>{formatResolution(value)}</button>)}</div>
        <div className="video-capability-fields"><label><span>自定义清晰度</span><input aria-label="支持清晰度" value={resolutionsText} onChange={(event) => { const value = event.target.value; setResolutionsText(value); const next = parseResolutions(value); if (!next.includes(defaultResolution)) setDefaultResolution(next[0] ?? ""); }} placeholder="例如：720p, 1080p" /></label><label><span>默认清晰度</span><select aria-label="默认清晰度" value={resolutions.includes(defaultResolution) ? defaultResolution : resolutions[0] ?? ""} disabled={!resolutions.length} onChange={(event) => setDefaultResolution(event.target.value)}>{resolutions.map((value) => <option value={value} key={value}>{formatResolution(value)}</option>)}</select></label></div>
      </section>
      {!canManage && <p className="studio-error" role="alert">当前账号没有模型编辑权限。请联系管理员，或前往<Link to="/settings/providers">模型渠道</Link>查看配置。</p>}
      {save.error && <p className="studio-error" role="alert">{toErrorMessage(save.error)}</p>}
    </div>
  </Dialog>;
}
