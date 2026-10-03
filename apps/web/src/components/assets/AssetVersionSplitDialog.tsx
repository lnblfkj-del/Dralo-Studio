import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  getAssetSplitInfo,
  getMediaObjectUrl,
  splitAssetVersion,
  type AssetSplitRegion,
} from "@/api/assets";
import { toErrorMessage } from "@/api/client";
import { subscribeToJob } from "@/api/jobs";
import { viewRegions, viewRegionsError, type ViewLayout } from "@/components/canvas/viewRegions";
import { Button, Dialog } from "@/components/ui";
import type { AssetViewType, Job } from "@/types/api";
import "@/styles/canvas-view-splitter.css";

const LABELS: Record<Exclude<AssetViewType, "layout_sheet">, string> = {
  base: "基础视图", appearance: "造型视图", expression: "表情视图",
  state: "状态视图", angle: "角度视图", environment: "环境视图", detail: "细节视图",
  first_frame: "首帧", last_frame: "尾帧", key_frame: "关键帧", storyboard_frame: "分镜参考帧",
};

function defaultType(layout: ViewLayout, allowed: AssetViewType[]): Exclude<AssetViewType, "layout_sheet"> {
  const preferred = layout === "expressions" ? "expression" : layout === "scene" ? "angle" : "angle";
  return (allowed.includes(preferred) ? preferred : allowed.find((value) => value !== "layout_sheet") ?? "base") as Exclude<AssetViewType, "layout_sheet">;
}

function waitForJob(job: Job): Promise<Job> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      controller.abort();
      reject(new Error("拆分任务等待超时，可在任务中心继续查看"));
    }, 10 * 60 * 1000);
    void subscribeToJob(job.id, controller.signal, (next) => {
      if (["queued", "running", "processing", "downloading", "retrying"].includes(next.status)) return;
      window.clearTimeout(timer);
      controller.abort();
      if (next.status === "succeeded") resolve(next);
      else reject(new Error(next.error_message || "排版图拆分失败"));
    }).catch((error) => {
      if ((error as Error)?.name !== "AbortError") {
        window.clearTimeout(timer);
        reject(error);
      }
    });
  });
}

export function AssetVersionSplitDialog({ projectId, assetId, versionId, assetName, assetType, onClose, onComplete }: {
  projectId: number;
  assetId: number;
  versionId: number;
  assetName: string;
  assetType: string;
  onClose: () => void;
  onComplete: () => void;
}) {
  const [layout, setLayout] = useState<ViewLayout>(assetType === "scene" ? "scene" : "three");
  const [regions, setRegions] = useState<AssetSplitRegion[]>([]);
  const [selected, setSelected] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [url, setUrl] = useState("");
  const [previewError, setPreviewError] = useState("");
  const info = useQuery({
    queryKey: ["asset-split-info", projectId, assetId, versionId],
    queryFn: () => getAssetSplitInfo(projectId, assetId, versionId),
    retry: false,
  });
  useEffect(() => {
    if (!info.data) return;
    const type = defaultType(layout, info.data.allowed_view_types);
    setRegions(viewRegions(layout, info.data).map((region) => ({ ...region, view_type: type })));
    setSelected(0);
    setConfirmed(false);
  }, [info.data, layout]);
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setUrl("");
    setPreviewError("");
    if (info.data) void getMediaObjectUrl(info.data.media_id).then((value) => {
      if (active) { objectUrl = value; setUrl(value); }
      else URL.revokeObjectURL(value);
    }).catch(() => { if (active) setPreviewError("排版预览读取失败"); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [info.data]);
  const validation = useMemo(() => info.data ? viewRegionsError(regions, info.data) : "", [info.data, regions]);
  const split = useMutation({
    mutationFn: async () => {
      const job = await splitAssetVersion(projectId, assetId, versionId, {
        request_id: crypto.randomUUID(),
        source_token: info.data!.source_token,
        regions: regions.map(({ label, view_type, x, y, width, height }) => ({
          label, view_type, x, y, width, height,
        })),
        confirmed: true,
      });
      return waitForJob(job);
    },
    onSuccess: () => { onComplete(); onClose(); },
  });
  const region = regions[selected];
  const change = (patch: Partial<AssetSplitRegion>) => {
    setRegions((current) => current.map((item, index) => index === selected ? { ...item, ...patch } : item));
    setConfirmed(false);
  };
  const error = info.error ?? split.error;

  return <Dialog open className="canvas-views-modal r5-asset-split-dialog" title="拆分排版预览" description={`${assetName} · 源图保留，裁切结果保存为同一资产的候选版本`} size="large" busy={split.isPending} onClose={onClose} footer={<><Button disabled={split.isPending} onClick={onClose}>取消</Button><Button variant="primary" loading={split.isPending} disabled={!info.data || !url || !!previewError || !!validation || !confirmed} onClick={() => split.mutate()}>确认拆分并保存版本</Button></>}>
    <div className="production-editor canvas-views-dialog">
      <div className="canvas-views-layout">
        <section className="canvas-views-preview">
          {info.data && url ? <div className="canvas-views-image" style={{ aspectRatio: `${info.data.width}/${info.data.height}` }}>
            <img src={url} alt="资产排版拆分源图" onError={() => setPreviewError("排版预览图片解码失败")} />
            {regions.map((item, index) => <button key={`${item.label}:${index}`} type="button" aria-label={`选中视图 ${index + 1}`} aria-pressed={selected === index} disabled={split.isPending} onClick={() => setSelected(index)} style={{ left: `${item.x / info.data!.width * 100}%`, top: `${item.y / info.data!.height * 100}%`, width: `${item.width / info.data!.width * 100}%`, height: `${item.height / info.data!.height * 100}%` }}><span>{index + 1} · {item.label}</span></button>)}
          </div> : <p>{info.isPending ? "正在读取排版预览…" : previewError || "排版预览不可用"}</p>}
          <p>模板只提供等分区域。请逐格核对边界、名称和资产分类；不会调用付费模型，也不会删除源排版图。</p>
        </section>
        <section className="canvas-views-settings"><fieldset disabled={split.isPending || !info.data}>
          <label>拆分模板<select aria-label="拆分模板" value={layout} onChange={(event) => setLayout(event.target.value as ViewLayout)}><option value="three">三视图 · 1 × 3</option><option value="expressions">表情九宫格 · 3 × 3</option><option value="scene">四视图 · 2 × 2</option></select></label>
          <label>当前视图<select aria-label="当前视图" value={selected} onChange={(event) => setSelected(Number(event.target.value))}>{regions.map((item, index) => <option key={index} value={index}>{index + 1} · {item.label}</option>)}</select></label>
          {region && <><label>版本名称<input aria-label="版本名称" maxLength={60} value={region.label} onChange={(event) => change({ label: event.target.value })} /></label><label>资产分类<select aria-label="资产分类" value={region.view_type} onChange={(event) => change({ view_type: event.target.value as AssetSplitRegion["view_type"] })}>{info.data?.allowed_view_types.filter((value) => value !== "layout_sheet").map((value) => <option key={value} value={value}>{LABELS[value as Exclude<AssetViewType, "layout_sheet">]}</option>)}</select></label><div className="canvas-views-fields">
            {([['x', '横坐标 X'], ['y', '纵坐标 Y'], ['width', '视图宽度'], ['height', '视图高度']] as const).map(([key, label]) => <label key={key}>{label}<input aria-label={label} type="number" min={key === "width" || key === "height" ? 2 : 0} step={1} value={region[key]} onChange={(event) => change({ [key]: Number(event.target.value) })} /></label>)}
          </div></>}
        </fieldset>
        <p>所有子图在同一事务中保存。任一裁切失败时整批不写入，成功后仍需在资产详情中人工采用。</p>
        <label className="canvas-views-confirm"><input type="checkbox" checked={confirmed} disabled={!info.data || !!validation || split.isPending} onChange={(event) => setConfirmed(event.target.checked)} />已核对全部区域、名称和分类，确认创建 {regions.length} 个候选版本</label>
        {(validation || error || previewError) && <p role="alert">{validation || previewError || toErrorMessage(error)}</p>}
        {split.isPending && <p role="status">正在本地拆分并保存资产版本…</p>}
        </section>
      </div>
    </div>
  </Dialog>;
}
