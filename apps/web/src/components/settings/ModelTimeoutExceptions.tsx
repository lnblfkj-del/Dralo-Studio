import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";

import { toErrorMessage } from "@/api/client";
import * as providersApi from "@/api/providers";
import type { Provider, ProviderModel } from "@/types/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui";

const fields = [
  { key: "request_timeout_seconds", label: "单次调用总超时（秒）", max: 3600 },
  { key: "first_byte_timeout_seconds", label: "首响应等待（秒）", max: 600 },
  { key: "stream_idle_timeout_seconds", label: "流式空闲超时（秒）", max: 900 },
] as const;

function savedTimeouts(model: ProviderModel) {
  return (model.default_params._text_execution ?? {}) as providersApi.TextTimeoutOverrides;
}

function TimeoutForm({ provider, model, defaults, onSaved, onCancel, onDirty }: {
  provider: Provider; model: ProviderModel; defaults: { firstByte: number; streamIdle: number };
  onSaved: () => void; onCancel: () => void; onDirty: (dirty: boolean) => void;
}) {
  const [initial] = useState(() => savedTimeouts(model));
  const [values, setValues] = useState(() => Object.fromEntries(fields.map(({ key }) => [key, String(initial[key] ?? "")])));
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  const dirty = fields.some(({ key }) => values[key] !== String(initial[key] ?? ""));
  useEffect(() => {
    onDirty(dirty || busy);
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    if (dirty || busy) window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, busy, onDirty]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    const overrides: providersApi.TextTimeoutOverrides = {};
    for (const { key, label, max } of fields) {
      const raw = (values[key] ?? "").trim();
      if (!raw) continue;
      const value = Number(raw);
      if (!Number.isInteger(value) || value < 5 || value > max) {
        setError(`${label}须为 5–${max} 之间的整数`); return;
      }
      overrides[key] = value;
    }
    const total = overrides.request_timeout_seconds;
    if (total && ((overrides.first_byte_timeout_seconds ?? 0) > total || (overrides.stream_idle_timeout_seconds ?? 0) > total)) {
      setError("首响应等待和流式空闲超时不能超过单次调用总超时"); return;
    }
    pending.current = true;
    setBusy(true);
    try {
      await providersApi.updateProviderModel(provider.id, model.id, { text_timeout_overrides: overrides });
      onSaved();
    } catch (reason) { setError(toErrorMessage(reason)); }
    finally { pending.current = false; setBusy(false); }
  };

  return <form onSubmit={save} className="execution-timeout-form">
    <fieldset disabled={busy} className="execution-field-grid">
      {fields.map(({ key, label, max }) => <label key={key}>{label}<input type="number" min={5} max={max} value={values[key]} onChange={event => setValues(current => ({ ...current, [key]: event.target.value }))} placeholder={String(key === "request_timeout_seconds" ? provider.timeout_seconds : key === "first_byte_timeout_seconds" ? defaults.firstByte : defaults.streamIdle)} /></label>)}
    </fieldset>
    {error && <p role="alert">{error}</p>}
    <div className="execution-timeout-actions"><Button disabled={busy} onClick={onCancel}>取消</Button><Button type="submit" variant="primary" icon={<Save size={16}/>} loading={busy} disabled={!dirty}>保存例外规则</Button></div>
  </form>;
}

export function ModelTimeoutExceptions({ firstByte, streamIdle }: { firstByte: number; streamIdle: number }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState("");
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  const user = useAuthStore(state => state.user);
  const canManage = user?.role === "admin" || !!user?.permissions?.["providers.edit"];
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["execution-timeout-models"], queryFn: providersApi.listProviders, enabled: open && !dirty, refetchOnWindowFocus: false });
  const models = (query.data ?? []).flatMap(provider => provider.models.filter(model => model.model_type === "text").map(model => ({ provider, model })));
  const current = models.find(({ model }) => String(model.id) === selected);

  return <details className="control-config-panel execution-timeout-exceptions" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>模型例外规则</summary>
    <div className="control-config-body">
      {open && query.isPending && <p>正在读取模型…</p>}
      {query.error && <p role="alert">{toErrorMessage(query.error)} <Button onClick={() => void query.refetch()}>重试</Button></p>}
      {query.isSuccess && <>
        <label className="execution-timeout-model">文本模型<select aria-label="例外规则模型" value={selected} disabled={!canManage || dirty} onChange={event => {setSelected(event.target.value); setMessage("");}}>
          <option value="">选择模型</option>
          {models.map(({ provider, model }) => <option key={model.id} value={model.id}>{provider.name} / {model.name}{fields.some(({ key }) => savedTimeouts(model)[key] != null) ? " · 已单独设置" : ""}</option>)}
        </select></label>
        {current && <><p className="execution-timeout-hint">留空使用默认值；全部清空并保存可恢复默认。仅影响后续新调用。</p><TimeoutForm key={selected} {...current} defaults={{ firstByte, streamIdle }} onDirty={setDirty} onCancel={() => {setSelected(""); setDirty(false);}} onSaved={() => {
          setSelected(""); setDirty(false); setMessage("模型例外规则已保存。");
          void cache.invalidateQueries({ queryKey: ["execution-timeout-models"] });
          void cache.invalidateQueries({ queryKey: ["providers"] });
          void cache.invalidateQueries({ queryKey: ["ai-settings"] });
        }} /></>}
        {!models.length && <p>暂无文本模型</p>}
      </>}
      {message && <p role="status">{message}</p>}
    </div>
  </details>;
}
