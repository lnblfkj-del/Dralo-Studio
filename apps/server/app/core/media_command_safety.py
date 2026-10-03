"""Restrict every FFmpeg input, including secondary audio and nested playlists."""

from app.core.config import settings


def local_media_arguments(arguments):
    if settings.runtime_execution_location != "cloud":
        return arguments
    result = []
    for item in arguments:
        if item == "-i":
            result.extend(["-protocol_whitelist", "file,pipe", "-max_alloc", "268435456"])
        result.append(item)
    return result
