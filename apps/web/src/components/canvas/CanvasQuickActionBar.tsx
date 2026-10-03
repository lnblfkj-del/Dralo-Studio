import { Clapperboard, FileText, Image, Map, Mic2, Music, Package, Shirt, UserRound, Video } from "lucide-react";

import type { CanvasNodeType } from "@/types/api";
import { nodePresentation } from "./nodePresentation";

const QUICK_ACTIONS: Array<{
  kind: CanvasNodeType;
  label: string;
  icon: typeof UserRound;
}> = [
  { kind: "text", label: "文本", icon: FileText },
  { kind: "character", label: "角色", icon: UserRound },
  { kind: "scene", label: "场景", icon: Map },
  { kind: "costume", label: "造型", icon: Shirt },
  { kind: "prop", label: "道具", icon: Package },
  { kind: "voice", label: "声音资产", icon: Mic2 },
  { kind: "director", label: "3D 导演台", icon: Clapperboard },
  { kind: "image", label: "图片", icon: Image },
  { kind: "video", label: "视频", icon: Video },
  { kind: "audio", label: "音频", icon: Music },
  { kind: "prompt", label: "镜头意图", icon: Clapperboard },
];

export function CanvasQuickActionBar({
  onAdd,
}: {
  onAdd: (kind: CanvasNodeType) => void;
}) {
  return (
    <div className="canvas-quick-action-bar" aria-label="快捷添加节点">
      <div className="quick-action-buttons">
        {QUICK_ACTIONS.map(({ kind, label }) => { const Icon = nodePresentation[kind].icon; return (
          <button
            key={kind}
            className="quick-action-btn"
            onClick={() => onAdd(kind)}
            title={`快速新建${label}`}
          >
            <span className="quick-action-icon-wrap"><Icon size={16} /></span>
            <strong>{label}</strong>
          </button>
        ); })}
      </div>
      <div className="quick-action-hint">
        <span className="quick-action-arrow">◇</span>
        点击快速新建
      </div>
    </div>
  );
}
