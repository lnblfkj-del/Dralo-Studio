import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toErrorMessage } from "@/api/client";
import * as api from "@/api/styleCategories";
import { SettingsButton, SettingsDialog, SettingsTabs } from "./SettingsPrimitives";
import "@/styles/style-categories.css";

export function useStyleCategories() {
  return useQuery({ queryKey: ["style-categories"], queryFn: api.listStyleCategories, refetchInterval: 15000 });
}

export function CategoryPicker({ value, onChange, categories, filter = false, excludeId, disabled = false }: {
  value: number | null | "all"; onChange: (id: number | null | "all") => void; categories: api.StyleCategory[];
  filter?: boolean; excludeId?: number; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const options = [...(filter ? [] : [{ id: null as number | null | "all", name: "不指定分类" }]), ...categories.filter(item => item.id !== excludeId)];
  if (filter) options.unshift({ id: "all", name: "全部" });
  const name = options.find(item => item.id === value)?.name ?? "未分类";
  return <div className="category-picker">
    {filter && <SettingsTabs label="风格分类" value={String(value)} items={options.map(item => ({ value: String(item.id), label: item.name }))} onChange={id => onChange(id === "all" ? "all" : Number(id))} />}
    {!filter && <SettingsButton disabled={disabled} onClick={() => setOpen(true)}>{name}</SettingsButton>}
    {open && <SettingsDialog title="选择分类" size="small" onClose={() => setOpen(false)}>
      <input className="category-search" aria-label="搜索分类" placeholder="搜索分类" value={search} onChange={e => setSearch(e.target.value)} />
      <div className="category-options">{options.filter(item => item.name.toLowerCase().includes(search.toLowerCase())).map(item => <button type="button" key={String(item.id)} aria-pressed={value === item.id} onClick={() => { onChange(item.id); setOpen(false); }}>{item.name}</button>)}</div>
    </SettingsDialog>}
  </div>;
}

export function CategoryNameDialog({ item, onClose, onCreated }: { item?: api.StyleCategory; onClose: () => void; onCreated?: (id: number) => void }) {
  const [name, setName] = useState(item?.name ?? "");
  const client = useQueryClient();
  const save = useMutation({ mutationFn: async () => {
    if (item) { await api.renameStyleCategory(item.id, name.trim()); return item.id; }
    return (await api.addStyleCategory(name.trim())).id;
  }, onSuccess: async id => { await client.invalidateQueries({ queryKey: ["style-categories"] }); onCreated?.(id); onClose(); } });
  return <SettingsDialog title={item ? "重命名分类" : "新增分类"} size="small" dirty={name !== (item?.name ?? "")} busy={save.isPending} onClose={onClose} onSubmit={e => { e.preventDefault(); if (name.trim()) save.mutate(); }} footer={requestClose => <><SettingsButton onClick={requestClose}>取消</SettingsButton><SettingsButton type="submit" primary disabled={!name.trim() || save.isPending}>保存</SettingsButton></>}>
    <label>分类名称<input className="category-search" autoFocus maxLength={64} value={name} onChange={e => setName(e.target.value)} /></label>
    {save.error && <p role="alert">{toErrorMessage(save.error)}</p>}
  </SettingsDialog>;
}

export function CategoryManager({ canEdit, canDelete, onClose }: { canEdit: boolean; canDelete: boolean; onClose: () => void }) {
  const query = useStyleCategories();
  const categories = query.data ?? [];
  const client = useQueryClient();
  const [editing, setEditing] = useState<api.StyleCategory | "new" | null>(null);
  const [removing, setRemoving] = useState<api.StyleCategory | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const refresh = async () => { await Promise.all([client.invalidateQueries({ queryKey: ["style-categories"] }), client.invalidateQueries({ queryKey: ["style-presets"] })]); };
  const order = useMutation({ mutationFn: api.orderStyleCategories, onSuccess: refresh });
  const remove = useMutation({ mutationFn: () => api.deleteStyleCategory(removing!.id, target), onSuccess: async () => { setRemoving(null); await refresh(); } });
  const move = (index: number, delta: number) => { const ids = categories.map(item => item.id); [ids[index], ids[index + delta]] = [ids[index + delta]!, ids[index]!]; order.mutate(ids); };
  return <SettingsDialog title="管理分类" onClose={onClose} busy={order.isPending} description="调整分类名称与显示顺序。删除分类时，风格会移入其他分类或未分类。">
    {canEdit && <SettingsButton primary onClick={() => setEditing("new")}>新增分类</SettingsButton>}
    {(query.error || order.error) && <p role="alert">{toErrorMessage(query.error || order.error)}</p>}
    <div className="category-manager-list">{categories.map((item, index) => <div key={item.id}>
      <span>{item.name}<small>{item.count} 个风格</small></span>
      {canEdit && <><button type="button" disabled={index === 0 || order.isPending} aria-label={`上移${item.name}`} onClick={() => move(index, -1)}>↑</button><button type="button" disabled={index === categories.length - 1 || order.isPending} aria-label={`下移${item.name}`} onClick={() => move(index, 1)}>↓</button><button type="button" onClick={() => setEditing(item)}>重命名</button></>}
      {canDelete && <button type="button" onClick={() => { setRemoving(item); setTarget(null); remove.reset(); }}>删除</button>}
    </div>)}</div>
    {editing && <CategoryNameDialog item={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    {removing && <SettingsDialog title={`删除分类 · ${removing.name}`} size="small" busy={remove.isPending} onClose={() => setRemoving(null)} footer={<><SettingsButton onClick={() => setRemoving(null)}>取消</SettingsButton><SettingsButton danger disabled={remove.isPending} onClick={() => remove.mutate()}>迁移风格并删除分类</SettingsButton></>}>
      <p>该分类当前有 {categories.find(item => item.id === removing.id)?.count ?? removing.count} 个风格。仅删除分类，所有风格和图片都会保留。</p>
      <p>将风格移至：</p><CategoryPicker categories={categories} value={target} excludeId={removing.id} onChange={id => setTarget(id === "all" ? null : id)} />
      {remove.error && <p role="alert">{toErrorMessage(remove.error)}</p>}
    </SettingsDialog>}
  </SettingsDialog>;
}
