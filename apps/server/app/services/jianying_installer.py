"""Jianying package naming, hashing, and no-overwrite installer script."""

import re
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.services.jianying_diagnostics import (
    DRAFT_PATH_TOKEN,
    DRAFT_ROOT_TOKEN,
    MEDIA_ROOT_TOKEN,
)

def _safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:100] or fallback


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _id() -> str:
    return str(uuid4()).upper()


def _token_path(root_token: str, relative_path: str) -> str:
    return root_token + "\\" + relative_path.replace("/", "\\")


def _powershell_literal(value: str) -> str:
    return value.replace("'", "''")


def build_installer_script(*, draft_id: str, root_name: str) -> bytes:
    """Build a no-overwrite installer for stable Jianying draft media paths."""
    script = r'''param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$packageRoot = $PSScriptRoot
if (-not $env:LOCALAPPDATA) {
    throw 'LOCALAPPDATA is unavailable; cannot locate Jianying Pro.'
}

$jianyingDataRoot = Join-Path $env:LOCALAPPDATA 'JianyingPro\User Data'
$draftRoot = Join-Path $jianyingDataRoot 'Projects\com.lveditor.draft'
$draftPath = Join-Path $draftRoot '__ROOT_NAME__'
$mediaBase = Join-Path $jianyingDataRoot 'WorksMedia'
$mediaPath = Join-Path $mediaBase '__DRAFT_ID__'

if (Test-Path -LiteralPath $draftPath) {
    throw "Refusing to overwrite existing Jianying draft: $draftPath"
}
if (Test-Path -LiteralPath $mediaPath) {
    throw "Refusing to overwrite existing Works media: $mediaPath"
}

$checksumPath = Join-Path $packageRoot 'checksums.sha256'
foreach ($line in Get-Content -LiteralPath $checksumPath -Encoding UTF8) {
    if (-not $line) { continue }
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') {
        throw "Invalid checksum record: $line"
    }
    $expected = $Matches[1]
    $relativePath = $Matches[2].Replace('/', [IO.Path]::DirectorySeparatorChar)
    $sourcePath = Join-Path $packageRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Package file is missing: $relativePath"
    }
    $actual = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "Package checksum mismatch: $relativePath"
    }
}

New-Item -ItemType Directory -Path $draftRoot -Force | Out-Null
New-Item -ItemType Directory -Path $mediaBase -Force | Out-Null
New-Item -ItemType Directory -Path $draftPath | Out-Null
New-Item -ItemType Directory -Path $mediaPath | Out-Null

$draftFiles = @(
    'draft_content.json',
    'draft_meta_info.json',
    'compatibility.json',
    'manifest.json',
    'checksums.sha256'
)
foreach ($name in $draftFiles) {
    Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination (Join-Path $draftPath $name)
}
Copy-Item -LiteralPath (Join-Path $packageRoot 'Timelines') -Destination (Join-Path $draftPath 'Timelines') -Recurse
if (Test-Path -LiteralPath (Join-Path $packageRoot 'videos')) {
    Copy-Item -LiteralPath (Join-Path $packageRoot 'videos') -Destination (Join-Path $mediaPath 'videos') -Recurse
}
if (Test-Path -LiteralPath (Join-Path $packageRoot 'audios')) {
    Copy-Item -LiteralPath (Join-Path $packageRoot 'audios') -Destination (Join-Path $mediaPath 'audios') -Recurse
}

$jsonMediaPath = $mediaPath.Replace('\', '\\')
$jsonDraftRoot = $draftRoot.Replace('\', '\\')
$jsonDraftPath = $draftPath.Replace('\', '\\')
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
Get-ChildItem -LiteralPath $draftPath -Filter '*.json' -File -Recurse | ForEach-Object {
    $content = [IO.File]::ReadAllText($_.FullName)
    $content = $content.Replace('__MEDIA_TOKEN__', $jsonMediaPath)
    $content = $content.Replace('__DRAFT_ROOT_TOKEN__', $jsonDraftRoot)
    $content = $content.Replace('__DRAFT_PATH_TOKEN__', $jsonDraftPath)
    [IO.File]::WriteAllText($_.FullName, $content, $utf8NoBom)
}

$marker = [ordered]@{
    schema_version = 'works.jianying.install.v1'
    installed_at = [DateTime]::UtcNow.ToString('o')
    draft_id = '__DRAFT_ID__'
    draft_path = $draftPath
    media_path = $mediaPath
    source_package = $packageRoot
    package_checksums_verified_before_install = $true
    installed_json_rewritten_after_verification = $true
    rename_safe = $true
}
$markerJson = $marker | ConvertTo-Json -Depth 4
[IO.File]::WriteAllText((Join-Path $draftPath 'works-installation.json'), $markerJson, $utf8NoBom)

Write-Host "Installed Jianying draft: $draftPath"
Write-Host "Installed stable media: $mediaPath"
Write-Host 'The draft can now be renamed inside Jianying without changing media paths.'
'''
    replacements = {
        "__ROOT_NAME__": _powershell_literal(root_name),
        "__DRAFT_ID__": _powershell_literal(draft_id),
        "__MEDIA_TOKEN__": MEDIA_ROOT_TOKEN,
        "__DRAFT_ROOT_TOKEN__": DRAFT_ROOT_TOKEN,
        "__DRAFT_PATH_TOKEN__": DRAFT_PATH_TOKEN,
    }
    for token, value in replacements.items():
        script = script.replace(token, value)
    # Windows PowerShell 5 reads scripts without a BOM using the active ANSI code page.
    return script.replace("\n", "\r\n").encode("utf-8-sig")


