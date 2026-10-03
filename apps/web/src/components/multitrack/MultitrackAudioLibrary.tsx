import { useState } from "react";
import { Music2, Plus } from "lucide-react";
import { AssetLibrarySearch } from "@/components/assets/AssetLibrarySearch";
import type { AudioCategory, MultitrackAudioItem } from "@/domain/multitrackAudioLibrary";
import { audioDragType } from "@/domain/editProjectAudioDrop";

export function MultitrackAudioLibrary({ items, loading, error, disabled, onAdd, projectId, searchValue, addedMediaIds = [] }: {
  items: MultitrackAudioItem[]; loading: boolean; error?: string; disabled: boolean;
  onAdd: (item: MultitrackAudioItem, category: AudioCategory) => void;
  projectId?: number;
  searchValue?: string;
  addedMediaIds?: number[];
}) {
  const [search, setSearch] = useState("");
  const [purpose, setPurpose] = useState<AudioCategory>("BGM");
  const visible = items.filter((item) => item.label.toLocaleLowerCase().includes((searchValue ?? search).trim().toLocaleLowerCase()));
  return <div className="multitrack-audio-library">
    {searchValue === undefined && <AssetLibrarySearch value={search} onChange={setSearch} label="搜索项目声音素材" />}
    {loading && <p role="status">正在读取声音素材...</p>}
    {error && <p role="alert">{error}</p>}
    <div className="multitrack-entry-list">{(["BGM", "环境音", "音效", "配音", "未分类"] as AudioCategory[]).map((category) => <details key={category} open>
      <summary>{category}</summary>
      {category === "未分类" && <label className="multitrack-audio-purpose">加入用途<select aria-label="未分类音频加入用途" value={purpose} onChange={(event) => setPurpose(event.target.value as AudioCategory)}>{["BGM", "环境音", "音效", "配音"].map((value) => <option key={value}>{value}</option>)}</select></label>}
      {visible.filter((item) => item.category === category).map((item) => <div className="multitrack-audio-item" key={item.id} draggable={!!projectId && !disabled && item.available} onDragStart={(event) => { if (!projectId || disabled || !item.available) { event.preventDefault(); return; } event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData(audioDragType, JSON.stringify({ projectId, mediaId: item.mediaId })); }}><Music2 size={15} /><span title={item.label}>{item.label}{addedMediaIds.includes(item.mediaId) && <small className="multitrack-added-badge">已加入</small>}{!item.available && <small>素材不可用</small>}</span><button aria-label={`加入 ${item.label}`} title="加入时间线" disabled={disabled || !item.available} onClick={() => onAdd(item, category === "未分类" ? purpose : category)}><Plus size={15} /></button></div>)}
      {!visible.some((item) => item.category === category) && <p>暂无素材</p>}
    </details>)}</div>
  </div>;
}
