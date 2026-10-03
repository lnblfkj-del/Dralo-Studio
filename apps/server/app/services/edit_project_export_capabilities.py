"""Conservative export gates; never substitute an old production-plan export."""
# ruff: noqa: RUF001

from app.services.episode_edit_contract import ClipStyle


def package_blockers(document, format):
    if format in {"mp4", "archive"}:
        return []
    target = "Premiere" if format == "premiere" else "剪映"
    issues = [f"{target} 新剪辑文档工程尚未通过实机导入验收，请使用 MP4 或剪辑工程归档"]
    if not document:
        return issues
    if any(clip.track == "subtitle" for clip in document.clips):
        issues.append(f"{target} 的多轨字幕、字体与描边映射尚未验证，不能忽略后导出")
    audio = [clip for clip in document.clips if clip.track not in {"video", "subtitle"}]
    if audio:
        issues.append(f"{target} 的音量、循环、淡入淡出与原声替换映射尚未验证")
    if any(
        (clip.style or ClipStyle()) != ClipStyle()
        for clip in document.clips
        if clip.track == "video"
    ):
        issues.append(f"{target} 的视频缩放、位置、旋转与原声设置映射尚未验证")
    return issues
