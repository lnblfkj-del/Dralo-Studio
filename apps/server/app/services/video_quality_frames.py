"""Bounded local frame sampling; no external model calls or media writes."""
import asyncio
import base64
import json
import subprocess

from app.core.config import settings
from app.core.errors import ConflictError
from app.models import MediaFile
from app.services.team_access import same_team
from app.services.video_preview_service import _duration, location


def sample(media, depth, edge=None):
    source, _, _ = location(media)
    duration = _duration(source)
    count = 1 if edge else (9 if depth == "detail" else 3)
    end = max(0, duration - 0.08)
    times = ([end if edge == "end" else 0] if edge else
             [round(end * index / (count - 1), 3) for index in range(count)])
    images = []
    for time in times:
        output = subprocess.run([
            settings.ffmpeg_path, "-nostdin", "-v", "error", "-protocol_whitelist", "file",
            "-ss", str(time), "-i", str(source), "-frames:v", "1",
            "-vf", "scale=768:768:force_original_aspect_ratio=decrease", "-threads", "1",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
        ], capture_output=True, timeout=30, check=True)
        if not output.stdout.startswith(b"\xff\xd8") or len(output.stdout) > 2_000_000:
            raise ConflictError("视频抽帧失败，未提交检查模型")
        images.append("data:image/jpeg;base64," + base64.b64encode(output.stdout).decode())
    return duration, times, images


async def prepare(session, job, model, provider):
    from app.services.asset_reference_service import load_reference_images
    if not provider.enabled or not model.enabled or "vision" not in (model.capabilities or []):
        raise ConflictError("检查模型已停用或不支持图片理解，未调用模型")
    review = dict(job.payload["review"])

    async def media_for(ident, expected_hash, expected_stamp):
        media = await session.get(MediaFile, ident)
        if (not media or media.kind != "video" or media.hash != expected_hash
                or not await same_team(session, job.owner_id, media.owner_id)):
            raise ConflictError("检查视频已变化或无权访问，未调用模型")
        if location(media)[2] != expected_stamp:
            raise ConflictError("检查视频文件已变化，请重新确认")
        return media

    media = await media_for(review["media_id"], review["media_hash"], review["media_stamp"])
    duration, times, images = await asyncio.to_thread(sample, media, review["depth"])
    labels = [f"图{index + 1}：当前片段 {time}秒" for index, time in enumerate(times)]
    for neighbor in review["neighbors"]:
        media = await media_for(neighbor["media_id"], neighbor["media_hash"], neighbor["media_stamp"])
        _, edge_times, edge_images = await asyncio.to_thread(sample, media, "quick", neighbor["edge"])
        labels.append(f"图{len(images) + 1}：相邻片段{neighbor['order']} {edge_times[0]}秒，仅供衔接参考")
        images.extend(edge_images)
    references = await load_reference_images(session, job, review["reference_media_ids"], max_images=6)
    for ident, image in zip(review["reference_media_ids"], references, strict=True):
        names = [item.get("asset_name") for item in review["bindings"] if item.get("media_file_id") == ident]
        labels.append(f"图{len(images) + 1}：实际提交参考素材 {ident}，关联名称 {names}")
        images.append(image)
    review.update(duration=duration, sample_times=times)
    job.payload = {**job.payload, "review": review,
                   "prompt": job.payload["prompt"] + "\n画面索引：" + json.dumps(labels, ensure_ascii=False)}
    return images
