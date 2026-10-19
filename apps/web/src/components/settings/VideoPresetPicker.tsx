import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, RefreshCw } from "lucide-react";
import { Button, Dialog } from "@/components/ui";
import * as api from "@/api/videoTools";
import { toErrorMessage } from "@/api/client";
import { videoPackageChanges } from './videoConfigImport';
import "@/styles/video-connection-tools.css";

const names: Record<string, string> = {text: "文字生成", single_image: "单图参考", first_frame: "首帧生成",
  first_last_frame: "首尾帧生成", multi_reference: "多图参考"};

export function VideoPackageSummary({value}: {value: api.VideoPackage}) {
  const modes = value.video_config.specification.modes;
  return <dl className="video-package-summary">
    <dt>生成方式</dt><dd>{[...new Set(modes.map(m => names[m.input_mode] ?? m.input_mode))].join("、")}</dd>
    <dt>支持组合</dt><dd>{modes.length} 组，时长和素材限制按组合生效</dd>
    <dt>画幅 / 分辨率</dt><dd>{[...new Set(modes.map(m => `${m.aspect_ratio} · ${m.resolution}`))].join("；")}</dd>
  </dl>;
}

export function VideoPresetPicker({providerId, identifier, current, onApply, onIdentifier, onClose}: {
  providerId: number; identifier: string; current?: () => api.VideoPackage;
  onApply: (value: api.VideoPackage) => void; onClose: () => void;
  onIdentifier?: (value: string) => void;
}) {
  const query = useQuery({queryKey: ["video-preset-catalog"], queryFn: () => api.listVideoPresets(), retry: false});
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState(identifier);
  const [selected, setSelected] = useState<api.VideoPreset & {confirm_interface: boolean} | null>(null);
  const [interfaceConfirmed, setInterfaceConfirmed] = useState(false);
  const [trust, setTrust] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  let before: api.VideoPackage | null = null;
  try { before = current?.() ?? null; } catch { /* An incomplete model can be repaired from a preset. */ }
  const changes = selected ? videoPackageChanges(before, selected.package).join('、') : '';
  const run = async (task: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { await task(); } catch (reason) { setError(toErrorMessage(reason)); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const items = query.data?.items.filter(item => `${item.name} ${item.source} ${item.model_ids.join(" ")} ${item.protocol}`.toLowerCase().includes(search.toLowerCase())) ?? [];
  return <Dialog open title="选择接入配置" size="medium" busy={busy} onClose={onClose}
    footer={<><Button onClick={onClose} disabled={busy}>取消</Button>{selected && <Button variant="primary" icon={<Check size={15} />}
      disabled={busy || (selected.confirm_interface && !interfaceConfirmed) || (!!selected.package.video_config.integration?.service_adapter && !trust)}
      onClick={() => void run(async () => {
        // Re-read the source immediately before applying so edited model sources cannot race the preview.
        const latest = await api.previewVideoPreset({key: selected.key, fingerprint: selected.fingerprint, provider_id: providerId, model_identifier: target});
        if (latest.package.video_config.integration?.service_adapter) await api.installVideoAdapter(providerId, latest.package);
        onIdentifier?.(target); onApply(structuredClone(latest.package)); onClose();
      })}>应用到编辑稿</Button>}</>}>
    <div className="video-preset-picker" data-local-tools>
      <label>搜索配置<input aria-label="搜索接入配置" value={search} onChange={event => setSearch(event.target.value)} placeholder="名称、渠道或模型" /></label>
      {!identifier && onIdentifier && <label>选择配置适用的模型<select aria-label="接入配置适用模型" value={target} disabled={busy} onChange={event => {setTarget(event.target.value); setSelected(null); setInterfaceConfirmed(false); setTrust(false);}}><option value="">请选择渠道提供的模型标识</option>{[...new Set(query.data?.items.flatMap(item => item.model_ids) ?? [])].map(id => <option key={id} value={id}>{id}</option>)}</select></label>}
      <div className="video-preset-target">当前模型：{target || "选择配置适用的模型"}<Button icon={<RefreshCw size={15} />} title="刷新配置目录" aria-label="刷新配置目录" disabled={busy} onClick={() => {setSelected(null); void query.refetch();}} /></div>
      {query.isPending && <p role="status">正在读取配置…</p>}
      {query.error && <p role="alert">{toErrorMessage(query.error)}</p>}
      <div className="video-preset-list">{items.map(item => {
        const matches = item.model_ids.includes(target);
        return <article key={item.key} data-preset-key={item.key}>
          <div><strong>{item.name}</strong><small>{item.source} · 版本 {item.version}</small><small>适用模型：{item.model_ids.join("、")}</small><small>{item.protocol} · 已声明，实测记录不随配置复制</small></div>
          <Button disabled={busy || !matches} title={matches ? "预览配置差异" : "不适用于当前模型"} onClick={() => void run(async () => {
            setSelected(null); setInterfaceConfirmed(false); setTrust(false);
            setSelected(await api.previewVideoPreset({key: item.key, fingerprint: item.fingerprint, provider_id: providerId, model_identifier: target}));
          })}>{matches ? "查看配置" : "模型不匹配"}</Button>
        </article>;
      })}</div>
      {query.data && !items.length && <p role="status">没有匹配配置，可导入供应商提供的 JSON 配置，或手动配置后另存。</p>}
      {selected && <section className="video-preset-preview"><strong>{selected.name} · {selected.version}</strong>
        <VideoPackageSummary value={selected.package} />
        <p>将替换：{changes || "配置内容相同"}。应用后仍需保存模型。</p>
        {selected.confirm_interface && <label className="video-preset-consent"><input type="checkbox" checked={interfaceConfirmed} onChange={event => setInterfaceConfirmed(event.target.checked)} />已核对当前渠道使用相同的视频接口与规格</label>}
        {!!selected.package.video_config.integration?.service_adapter && <label className="video-preset-consent"><input type="checkbox" checked={trust} onChange={event => setTrust(event.target.checked)} />确认适配服务由我方部署或信任；将接收提示词和素材</label>}
        <details><summary>查看完整配置</summary><pre>{JSON.stringify(selected.package, null, 2)}</pre></details>
      </section>}
      {error && <p role="alert" className="video-check-error">{error}</p>}
    </div>
  </Dialog>;
}

export function SaveVideoPreset({identifier, getPackage, onClose}: {identifier: string; getPackage: () => api.VideoPackage; onClose: () => void}) {
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const inFlight = useRef(false);
  return <Dialog open title="另存为接入配置" size="small" busy={busy} onClose={onClose} footer={<>
    <Button disabled={busy} onClick={onClose}>{done ? "关闭" : "取消"}</Button>
    {!done && <Button variant="primary" disabled={busy || !name.trim() || !source.trim() || !version.trim() || !identifier.trim()} onClick={async () => {
      if (inFlight.current) return;
      inFlight.current = true; setBusy(true); setError("");
      try { await api.saveVideoPreset({name: name.trim(), version: version.trim(), source: source.trim(), model_ids: [identifier.trim()], package: getPackage()}); setDone(true); }
      catch (reason) { setError(toErrorMessage(reason)); }
      finally { setBusy(false); inFlight.current = false; }
    }}>保存到配置目录</Button>}</>}>
    <div className="video-preset-picker" data-local-tools>{done ? <p role="status">已另存为工作区配置，当前模型尚未保存。</p> : <>
      <label>配置名称<input aria-label="接入配置名称" maxLength={80} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>版本号<input aria-label="接入配置版本号" maxLength={40} value={version} onChange={e => setVersion(e.target.value)} /></label>
      <label>来源说明<input aria-label="接入配置来源" maxLength={200} placeholder="例如：渠道商提供，2026-10-15核对" value={source} onChange={e => setSource(e.target.value)} /></label>
      <p>适用模型：{identifier || "请先填写模型名称"}</p>
    </>}{error && <p role="alert" className="video-check-error">{error}</p>}</div>
  </Dialog>;
}
