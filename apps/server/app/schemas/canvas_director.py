"""Bounded director documents and keyframes. No scripts or remote resources."""
# ruff: noqa: N815 -- StoryAI's versioned wire format uses camelCase.

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Transform(Strict):
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]

    @model_validator(mode="after")
    def bounds(self):
        if any(abs(v) > 10000 for v in (*self.position, *self.rotation, *self.scale)):
            raise ValueError("Transform exceeds scene limits")
        return self


class Rig(Strict):
    rigType: Literal["mannequin", "ue4-mannequin", "mixamo", "vrm", "custom-humanoid"]
    posePresetId: str | None = Field(default=None, max_length=80)
    actionPresetId: str | None = Field(default=None, max_length=80)
    controls: dict[str, float] = Field(default_factory=dict, max_length=100)

class Asset(Strict):
    id: str = Field(min_length=1, max_length=100)
    kind: Literal["character", "scene", "prop", "panorama"]
    sourceType: Literal["model", "image"]
    fileName: str = Field(min_length=1, max_length=300)
    name: str | None = Field(default=None, max_length=300)
    url: str = Field(min_length=1, max_length=2000)
    mediaId: int | None = Field(default=None, gt=0)
    hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    version: int = Field(default=1, ge=1)
    origin: Literal["packaged", "local-upload", "project-media"] = "packaged"
    licenseStatus: Literal["unknown", "test-only", "owned", "redistributable"] = "unknown"
    assetSource: Literal["local", "library"] | None = None
    projectionMode: Literal["equirectangular", "backdrop"] | None = None
    characterRigType: Literal["mannequin", "ue4-mannequin", "mixamo", "vrm", "custom-humanoid"] | None = None
    characterImportReadiness: Literal["ready", "native-only", "manual-mapping", "static-only"] | None = None
    characterOrientationCorrection: tuple[float, float, float] | None = None

    @model_validator(mode="after")
    def trusted_url(self):
        if self.mediaId is not None:
            if self.url != f"/api/media/{self.mediaId}":
                raise ValueError("Stored Director asset URL does not match its media ID")
        elif not self.url.startswith("/director/"):
            raise ValueError("Director assets must use packaged or synchronized project resources")
        return self


class ObjectMotionKeyframe(Strict):
    id: str = Field(min_length=1, max_length=100)
    time: float = Field(ge=0, le=1)
    transform: Transform
    actionPresetId: str | None = Field(default=None, max_length=80)
    facingMode: Literal["path", "manual"] = "manual"
    pointBehavior: Literal["pass", "hold"] = "pass"
    holdSeconds: float = Field(default=0, ge=0, le=15)
    holdAction: Literal["stand", "current", "custom"] = "current"
    holdActionPresetId: str | None = Field(default=None, max_length=80)


class ObjectMotionPath(Strict):
    loop: bool = False
    interpolation: Literal["linear", "smooth"] = "smooth"
    speedMode: Literal["uniform", "soft", "custom"] | None = None
    customEasing: tuple[float, float, float, float] | None = None
    keyframes: list[ObjectMotionKeyframe] = Field(default_factory=list, max_length=150)

    @model_validator(mode="after")
    def ordered(self):
        times = [item.time for item in self.keyframes]
        if times != sorted(times) or len({item.id for item in self.keyframes}) != len(self.keyframes):
            raise ValueError("Invalid object route ordering or IDs")
        return self


class Object(Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=200)
    kind: Literal["character", "scene", "prop", "camera"]
    visible: bool
    locked: bool
    transform: Transform
    bodyType: (
        Literal["mannequin", "female", "broad", "muscular", "slim", "teen", "child", "chibi"] | None
    ) = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    assetRefId: str | None = Field(default=None, max_length=100)
    geometryType: Literal["box", "sphere", "cylinder", "torus", "cone", "pyramid"] | None = None
    crowdId: str | None = Field(default=None, max_length=100)
    crowdLabel: str | None = Field(default=None, max_length=200)
    linkedCameraId: str | None = Field(default=None, max_length=100)
    characterRig: Rig | None = None
    motionPath: ObjectMotionPath | None = None


class CameraMotionKeyframe(Strict):
    id: str = Field(min_length=1, max_length=100)
    time: float = Field(ge=0, le=1)
    position: tuple[float, float, float]
    target: tuple[float, float, float]
    fov: float = Field(gt=0, lt=180)
    targetMode: Literal["manual", "object"] = "manual"
    targetObjectId: str | None = Field(default=None, max_length=100)
    pointBehavior: Literal["pass", "hold"] = "pass"
    holdSeconds: float = Field(default=0, ge=0, le=15)


class CameraMotionPath(Strict):
    duration: float = Field(gt=0, le=15)
    loop: bool = False
    interpolation: Literal["linear", "smooth"] = "smooth"
    easing: Literal["linear", "ease-in-out"] = "ease-in-out"
    speedMode: Literal["uniform", "soft", "custom"] | None = None
    customEasing: tuple[float, float, float, float] | None = None
    keyframes: list[CameraMotionKeyframe] = Field(default_factory=list, max_length=150)

    @model_validator(mode="after")
    def ordered(self):
        times = [item.time for item in self.keyframes]
        if times != sorted(times) or len({item.id for item in self.keyframes}) != len(self.keyframes):
            raise ValueError("Invalid camera route ordering or IDs")
        return self


class Camera(Strict):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(max_length=200)
    fov: float = Field(gt=0, lt=180)
    transform: Transform
    targetMode: Literal["manual", "object"]
    targetObjectId: str | None = Field(default=None, max_length=100)
    target: tuple[float, float, float]
    motionPath: CameraMotionPath | None = None


class Scene(Strict):
    scale: float = Field(gt=0, le=10000)
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    backgroundColor: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    backgroundBrightness: float = Field(default=1, ge=0, le=2)
    panoramaYaw: float
    panoramaRadius: float = Field(gt=0, le=10000)
    showLabels: bool
    snapToGrid: bool
    showGrid: bool = True
    showGround: bool
    groundOpacity: float = Field(ge=0, le=1)
    groundHeight: float = Field(ge=-10000, le=10000)
    pathCollisionEnabled: bool = False


class Project(Strict):
    timeline: Timeline | None = None
    version: Literal[1, 2]
    scene: Scene
    assets: list[Asset] = Field(default_factory=list, max_length=300)
    objects: list[Object] = Field(max_length=300)
    cameras: list[Camera] = Field(max_length=30)
    activeCameraId: str | None
    panoramaAssetId: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def references(self):
        objects = {o.id for o in self.objects}
        cameras = {c.id for c in self.cameras}
        assets = {a.id for a in self.assets}
        if len(objects) != len(self.objects) or len(cameras) != len(self.cameras):
            raise ValueError("Duplicate object or camera IDs")
        if self.activeCameraId is not None and self.activeCameraId not in cameras:
            raise ValueError("Active camera does not exist")
        if any(c.targetObjectId and c.targetObjectId not in objects for c in self.cameras):
            raise ValueError("Camera target does not exist")
        if any(o.linkedCameraId and o.linkedCameraId not in cameras for o in self.objects):
            raise ValueError("Linked camera does not exist")
        if any(o.assetRefId and o.assetRefId not in assets for o in self.objects):
            raise ValueError("Object asset does not exist")
        if self.panoramaAssetId is not None and self.panoramaAssetId not in assets:
            raise ValueError("Panorama asset does not exist")
        if self.timeline:
            targets = set()
            for track in self.timeline.tracks:
                identity = (track.kind, track.targetId)
                if identity in targets or track.targetId not in (
                    cameras if track.kind == "camera" else objects
                ):
                    raise ValueError("Duplicate or missing animation target")
                targets.add(identity)
                if (
                    track.kind == "object"
                    and next(o for o in self.objects if o.id == track.targetId).kind == "camera"
                ):
                    raise ValueError("Camera objects require a camera track")
                frames = [k.frame for k in track.keys]
                if frames != sorted(set(frames)) or any(
                    f >= self.timeline.durationFrames for f in frames
                ):
                    raise ValueError("Invalid keyframe ordering or range")
        return self


class Keyframe(Strict):
    frame: int = Field(ge=0, le=449, strict=True)
    transform: Transform
    controls: dict[str, float] | None = Field(default=None, max_length=100)
    actionPresetId: str | None = Field(default=None, max_length=80)
    target: tuple[float, float, float] | None = None
    fov: float | None = Field(default=None, gt=0, lt=180)


class Track(Strict):
    kind: Literal["object", "camera"]
    targetId: str = Field(min_length=1, max_length=100)
    interpolation: Literal["linear", "smooth", "step"]
    keys: list[Keyframe] = Field(max_length=150)

    @model_validator(mode="after")
    def camera_fields(self):
        if self.kind == "camera" and any(k.target is None or k.fov is None for k in self.keys):
            raise ValueError("Camera keyframes require target and fov")
        return self


class Timeline(Strict):
    fps: Literal[24, 30]
    durationFrames: int = Field(ge=24, le=450, strict=True)
    tracks: list[Track] = Field(max_length=100)

    @model_validator(mode="after")
    def duration_limit(self):
        if self.durationFrames > self.fps * 15:
            raise ValueError("Previsualization is limited to 15 seconds")
        return self


Project.model_rebuild()


class DirectorView(Strict):
    fov: float = Field(gt=0, lt=180)
    position: tuple[float, float, float]
    target: tuple[float, float, float]


class DirectorState(Strict):
    currentFrame: int = Field(default=0, ge=0, le=449, strict=True)
    animationPreview: bool = False
    project: Project
    directorView: DirectorView | None = None
    viewMode: Literal["director", "camera"] = "director"
    viewportAspectRatio: Literal["auto", "2:1", "16:9", "9:16", "1:1", "4:3", "3:4", "21:9"] = (
        "16:9"
    )
    viewportRuleOfThirdsEnabled: bool = True

    @model_validator(mode="after")
    def current_frame_bounds(self):
        if self.project.timeline and self.currentFrame >= self.project.timeline.durationFrames:
            raise ValueError("Current frame is outside the timeline")
        return self


class DirectorSave(Strict):
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=64)
    state: DirectorState


class DirectorCapture(Strict):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=64)
    data_url: str = Field(max_length=12_000_000)
    name: str = Field(default="3D 机位截图", min_length=1, max_length=200)
