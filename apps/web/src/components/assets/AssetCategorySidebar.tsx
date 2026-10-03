import { Box, Brush, Image, Map, Mic2, Music2, PanelLeftClose, PanelLeftOpen, Shirt, UserRound, Video } from "lucide-react";

import type { AssetType } from "@/types/api";

export const ASSET_TYPES: Array<{ key: AssetType; label: string; icon: typeof UserRound }> = [
  { key: "character", label: "角色", icon: UserRound },
  { key: "scene", label: "场景", icon: Map },
  { key: "prop", label: "道具", icon: Box },
  { key: "costume", label: "服装 / 造型", icon: Shirt },
  { key: "voice", label: "声音", icon: Music2 },
  { key: "video", label: "视频", icon: Video },
  { key: "canvas", label: "画布", icon: Brush },
  { key: "reference", label: "镜头帧", icon: Image },
];

const SUBTYPES: Partial<Record<AssetType, Array<{ value: string; label: string }>>> = {
  character: [
    { value: "lead", label: "主角" }, { value: "supporting", label: "配角" },
    { value: "extra", label: "群演" }, { value: "unclassified", label: "未分类" },
  ],
  voice: [
    { value: "voice", label: "角色声音 / 配音" }, { value: "music", label: "配乐" },
    { value: "ambience", label: "环境声" }, { value: "sfx", label: "音效" },
    { value: "unclassified", label: "未分类" },
  ],
};

export function AssetCategorySidebar({ type, subtype, counts, collapsed = false, onType, onSubtype, onExtract, onCollapse }: {
  type: AssetType;
  subtype: string;
  counts: Record<string, number>;
  collapsed?: boolean;
  onType: (type: AssetType) => void;
  onSubtype: (subtype: string) => void;
  onExtract: () => void;
  onCollapse?: () => void;
}) {
  const children = SUBTYPES[type] ?? [];
  return <aside className={`r5-category-panel ${collapsed ? "collapsed" : ""}`} aria-label="资产分类">
    <header className="r5-panel-heading">
      <div><strong>资产分类</strong></div>
      {onCollapse && <button aria-label={collapsed ? "展开资产分类" : "收起资产分类"} title={collapsed ? "展开资产分类" : "收起资产分类"} onClick={onCollapse}>{collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}</button>}
    </header>
    <div className="r5-category-list">
      {ASSET_TYPES.map(({ key, label, icon: Icon }) => <div key={key}>
        <button title={collapsed ? label : undefined} aria-pressed={type === key && !subtype} className={type === key && !subtype ? "active" : ""} onClick={() => onType(key)}>
          <Icon size={16} /><span>{label}</span><small>{counts[key] ?? 0}</small>
        </button>
        {type === key && children.length > 0 && <div className="r5-subcategory-list">
          {children.map((item) => <button key={item.value} aria-pressed={subtype === item.value} className={subtype === item.value ? "active" : ""} onClick={() => onSubtype(item.value)}><span>{item.label}</span></button>)}
        </div>}
      </div>)}
    </div>
    <div className="r5-category-actions">
      <button onClick={onExtract}><Mic2 size={15} />从剧本提取资产</button>
    </div>
  </aside>;
}
