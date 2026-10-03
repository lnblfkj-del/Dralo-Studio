import type { MediaOperation, ProcessingInfo } from "@/api/canvas";

export function processingError(op: MediaOperation, info: ProcessingInfo): string {
  if (info.rotation) return "源视频带有方向元数据，请先导入已规范方向的视频。";
  if (op.kind === "crop") {
    if (![op.x, op.y, op.width, op.height].every(Number.isInteger) || op.x < 0 || op.y < 0 || op.width < 2 || op.height < 2 || op.x + op.width > info.width || op.y + op.height > info.height) return "裁剪区域必须在原始画面内，宽高至少 2 像素。";
    if (info.kind === "video" && [op.x, op.y, op.width, op.height].some((v) => v % 2)) return "视频裁剪坐标、宽高必须是偶数。";
  }
  if (op.kind === "trim" && (![op.start, op.end].every(Number.isFinite) || op.start < 0 || op.end > info.duration || op.end - op.start < 0.1)) return "请选择素材时长内、至少 0.1 秒的片段。";
  if (op.kind === "frame" && (!Number.isFinite(op.at) || op.at < 0 || op.at >= info.duration)) return "抽帧时间必须小于视频总时长。";
  if (op.kind === "extract_audio" && !info.has_audio) return "这个视频没有音轨。";
  if (op.kind === "mix_audio") {
    const audio = info.audio_tracks?.find((item) => item.media_id === op.audio_media_id);
    if (!audio) return "请选择已连接到当前视频的后期音轨。";
    if (![op.start, op.trim_start, op.trim_end, op.volume].every(Number.isFinite) || op.start < 0 || op.trim_start < 0 || op.trim_end - op.trim_start < 0.1 || op.trim_end > audio.duration || op.start + op.trim_end - op.trim_start > info.duration + 0.01 || op.volume < 0 || op.volume > 1) return "音轨裁切、放置或音量超出允许范围。";
  }
  return "";
}
