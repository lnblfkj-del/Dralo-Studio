"""Jianying target constants and local installation diagnostics."""

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

TARGET_TYPE = "episode_jianying_draft"
SCHEMA_VERSION = "episode_jianying_draft.v1"
TARGET_APP = "剪映专业版"
TARGET_VERSION = "11.4.1.14443"
INSTALLER_FILENAME = "install-jianying-draft.ps1"
INSTALL_README_FILENAME = "安装说明.txt"
MEDIA_ROOT_TOKEN = "__WORKS_JIANYING_MEDIA_ROOT__"
DRAFT_ROOT_TOKEN = "__WORKS_JIANYING_DRAFT_ROOT__"
DRAFT_PATH_TOKEN = "__WORKS_JIANYING_DRAFT_PATH__"
REFERENCE_VALIDATION = {
    "status": "passed_with_limitations",
    "validated_at": "2026-09-17",
    "video_timeline_version": "11.4.1.14443",
    "audio_subtitle_version": "11.4.7.14462",
    "scope": [
        "draft_discovery",
        "five_clip_timeline",
        "embedded_media",
        "external_audio_tracks",
        "editable_subtitles",
        "edit_save_close_reopen",
    ],
}
REFERENCE_VALIDATED_VERSIONS = sorted(
    {
        REFERENCE_VALIDATION["video_timeline_version"],
        REFERENCE_VALIDATION["audio_subtitle_version"],
    }
)
RENAME_WARNING = (
    "只有运行包内安装脚本后才能在剪映内安全重命名草稿; "
    "不要把ZIP内容直接复制到剪映草稿目录。"
)


def _local_draft_root() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return (
        Path(local_app_data)
        / "JianyingPro"
        / "User Data"
        / "Projects"
        / "com.lveditor.draft"
    )


def _local_apps_root() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    return Path(local_app_data) / "JianyingPro" / "Apps"


def installation_diagnostics() -> dict[str, Any]:
    """Describe runnable Jianying versions without mutating the installation."""
    apps_root = _local_apps_root()
    installed_versions: list[dict[str, Any]] = []
    active_version: str | None = None
    release_type: str | None = None

    if apps_root and apps_root.is_dir():
        packet_path = apps_root / "JianyingProPacket.xml"
        if packet_path.is_file():
            try:
                packet = ET.parse(packet_path).getroot()
                version_node = packet.find("./full_appver")
                release_node = packet.find("./release_type")
                active_version = (
                    version_node.attrib.get("value") if version_node is not None else None
                )
                release_type = (
                    release_node.attrib.get("value") if release_node is not None else None
                )
            except (ET.ParseError, OSError):
                pass

        for version_path in sorted(apps_root.iterdir()):
            if not version_path.is_dir() or not re.fullmatch(
                r"\d+\.\d+\.\d+\.\d+", version_path.name
            ):
                continue
            files = [item for item in version_path.rglob("*") if item.is_file()]
            runnable = (version_path / "JianyingPro.exe").is_file()
            delta_files = sum(
                bool(re.search(r"_d_[0-9a-f]+$", item.name, flags=re.IGNORECASE))
                for item in files
            )
            if runnable:
                layout = "complete"
            elif files and delta_files == len(files):
                layout = "delta_cache"
            else:
                layout = "incomplete"
            installed_versions.append(
                {
                    "version": version_path.name,
                    "runnable": runnable,
                    "layout": layout,
                    "file_count": len(files),
                }
            )

    runnable_versions = {
        item["version"] for item in installed_versions if item["runnable"]
    }
    if not active_version and len(runnable_versions) == 1:
        active_version = next(iter(runnable_versions))
    target_exact_match_available = TARGET_VERSION in runnable_versions
    active_version_validated = active_version in REFERENCE_VALIDATED_VERSIONS

    if target_exact_match_available:
        status = "target_available"
        message = f"本机可运行目标版本 {TARGET_VERSION}。"
    elif active_version in runnable_versions and active_version_validated:
        status = "reference_validated"
        message = (
            f"本机当前可运行版本为 {active_version}, 已纳入参考实机验证; "
            f"目标版本 {TARGET_VERSION} 仅用于草稿结构。"
        )
    elif runnable_versions:
        status = "version_unvalidated"
        message = "检测到可运行剪映版本, 但该版本尚未完成本系统参考实机验证。"
    elif installed_versions:
        status = "incomplete_only"
        message = "仅检测到更新差分或不完整目录, 没有可运行的剪映专业版。"
    else:
        status = "not_detected"
        message = "未检测到剪映专业版安装版本。"

    return {
        "status": status,
        "active_version": active_version,
        "release_type": release_type,
        "target_exact_match_available": target_exact_match_available,
        "active_version_reference_validated": active_version_validated,
        "reference_validated_versions": REFERENCE_VALIDATED_VERSIONS,
        "installed_versions": installed_versions,
        "message": message,
    }


