/** Validate the actual container, not the requested duration label. */
export async function validateRecordedVideo(blob: Blob, expectedSeconds: number) {
  const url = URL.createObjectURL(blob);
  const video = document.createElement("video");
  try {
    return await new Promise<number>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("预演视频解码超时")), 15000);
      video.onloadedmetadata = () => {
        window.clearTimeout(timer);
        const duration = video.duration;
        if (!Number.isFinite(duration) || Math.abs(duration - expectedSeconds) > 0.5) {
          reject(new Error(`预演录制时长异常（目标 ${expectedSeconds} 秒，实际 ${Number.isFinite(duration) ? duration.toFixed(2) : "未知"} 秒）。请关闭高负载页面后重试；异常结果未交付。`));
        } else resolve(duration);
      };
      video.onerror = () => { window.clearTimeout(timer); reject(new Error("预演视频无法解码")); };
      video.src = url;
    });
  } finally {
    video.onloadedmetadata = null; video.onerror = null;
    video.removeAttribute("src"); video.load();
    URL.revokeObjectURL(url);
  }
}
