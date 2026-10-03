import { Box, Boxes, Clapperboard, FileText, Film, Frame, Image, Layers, Map, MessageSquareText, Mic2, Music, Package, Shirt, Sparkles, UserRound, Video } from "lucide-react";
import type { CanvasNodeType } from "@/types/api";

export const nodePresentation: Record<CanvasNodeType, { label: string; icon: typeof Box }> = {
  director: { label: "3D 导演台", icon: Box },
  multitrack: { label: "多轨剪辑", icon: Film },
  text: { label: "创作文本", icon: MessageSquareText },
  character: { label: "角色", icon: UserRound }, scene: { label: "场景", icon: Map },
  costume: { label: "服装 / 造型", icon: Shirt }, prop: { label: "道具", icon: Package }, voice: { label: "声音资产", icon: Mic2 },
  image: { label: "图片", icon: Image }, video: { label: "视频", icon: Video }, audio: { label: "音频", icon: Music },
  prompt: { label: "镜头意图", icon: Sparkles }, file: { label: "文件", icon: FileText },
  frame: { label: "工作分组", icon: Frame }, output: { label: "输出", icon: Layers },
  episode: { label: "剧集", icon: Layers }, shot: { label: "分镜", icon: Clapperboard }, segment: { label: "视频片段", icon: Film }, asset: { label: "资产", icon: Boxes },
};
