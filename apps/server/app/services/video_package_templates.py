"""Customer-downloadable, importable examples. They declare examples, not verified capabilities."""

from copy import deepcopy


def example(*, service=False):
    connection = {"schema_version": "video-connection.v1", "name": "异步视频接口示例",
        "authentication": "bearer", "submit_path": "/render/tasks",
        "request": {"model": "$model", "prompt": "$prompt", "duration": "$duration",
                    "resolution": "$resolution", "aspect_ratio": "$aspect_ratio"},
        "task_id_path": "data.task_id", "poll_path": "/render/tasks/{task_id}", "status_path": "data.status",
        "succeeded": ["done"], "failed": ["failed", "cancelled"], "processing": ["queued", "processing"],
        "result_url_path": "data.video_url", "poll_seconds": 10}
    if service:
        connection["service_adapter"] = {"id": "example-video-adapter", "version": "1.0.0"}
    return deepcopy({"schema_version": "video-package.v1", "name": "独立视频适配服务示例" if service else "普通视频接口示例",
        "protocol": "custom_video", "video_config": {"schema_version": "video-model.v1", "integration": connection,
        "specification": {"modes": [{"key": "text-720-16x9", "input_mode": "text", "aspect_ratio": "16:9",
            "resolution": "720p", "durations": {"kind": "discrete", "values_ms": [5000]}, "max_shots": 1,
            "reference_limits": [], "max_total_references": 0, "native_dialogue": False, "native_audio": False,
            "bgm_control": "prompt_preference"}]},
        "generation_defaults": {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"},
        "planning_policy": {}, "transport_params": {}}})
