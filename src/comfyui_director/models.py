"""Shared contracts. Creative plans never contain credentials or arbitrary workflows."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Canvas(StrictModel):
    width: int = Field(default=1024, ge=64, le=8192, multiple_of=8)
    height: int = Field(default=1024, ge=64, le=8192, multiple_of=8)


class VisualBible(StrictModel):
    style: str = Field(min_length=1)
    palette: str = Field(min_length=1)
    lighting: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    composition: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)


class PromptSpec(StrictModel):
    positive_zh: str = Field(min_length=1)
    positive_en: str = Field(min_length=1)
    negative: str = ""
    acceptance: list[str] = Field(min_length=1)


class Placement(StrictModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)
    feather_px: float = Field(default=0, ge=0, le=32)
    instructions: str = Field(min_length=1)
    acceptance: list[str] = Field(min_length=1)


class TextLayer(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    font_size: int = Field(default=48, ge=8, le=512)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")


class VideoSpec(StrictModel):
    positive_zh: str = Field(min_length=1)
    positive_en: str = Field(min_length=1)
    negative: str = ""
    duration_seconds: float = Field(default=5, gt=0, le=60)
    fps: int = Field(default=16, ge=1, le=120)
    frames: int = Field(default=81, ge=1, le=1441)
    acceptance: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_duration(self):
        # A frame count may need to satisfy a model's 4n+1 constraint.
        if abs(self.frames / self.fps - self.duration_seconds) > 1 / self.fps + 0.01:
            raise ValueError("video frames/fps must match duration_seconds within one frame")
        return self


class ForegroundLayer(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
    prompt: PromptSpec
    placement: Placement


class CreativePlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    title: str = Field(min_length=1)
    request: str = Field(min_length=1)
    medium: Literal["image", "video"] = "image"
    canvas: Canvas
    visual: VisualBible
    full_image_prompt: PromptSpec
    background: PromptSpec
    foreground: PromptSpec
    placement: Placement
    extra_layers: list[ForegroundLayer] = Field(default_factory=list, max_length=31)
    text_layers: list[TextLayer] = Field(default_factory=list)
    video: VideoSpec | None = None
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_layout(self):
        placements = [self.placement, *(layer.placement for layer in self.extra_layers)]
        for placement in placements:
            if placement.x + placement.width > self.canvas.width:
                raise ValueError("foreground placement exceeds canvas width")
            if placement.y + placement.height > self.canvas.height:
                raise ValueError("foreground placement exceeds canvas height")
        ids = [layer.id for layer in self.extra_layers]
        if len(set(ids)) != len(ids) or set(ids) & {"background", "foreground", "compose", "video"}:
            raise ValueError("extra layer ids must be unique and must not use reserved task ids")
        if any(t.x >= self.canvas.width or t.y >= self.canvas.height for t in self.text_layers):
            raise ValueError("text origin must be inside canvas")
        if (self.medium == "video") != (self.video is not None):
            raise ValueError("video is required only when medium=video")
        return self


class OutputSelector(StrictModel):
    node: str
    field: Literal["images", "gifs", "videos", "audio"] = "images"
    index: int = Field(default=0, ge=0)


class WorkflowProfile(StrictModel):
    file: str
    # Semantic name -> [node id, input name]. No eval, string substitution, or generated code.
    bindings: dict[str, tuple[str, str]]
    outputs: dict[str, OutputSelector]
    defaults: dict[str, Any] = Field(default_factory=dict)


class Worker(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    url: str
    roles: list[Literal["background", "foreground", "video"]] = Field(min_length=1)
    capacity: int = Field(default=1, ge=1, le=16)
    api_key_env: str | None = None

    @model_validator(mode="after")
    def validate_url(self):
        from urllib.parse import urlsplit

        parsed = urlsplit(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("worker url must be http(s)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("worker URL must not contain credentials, query, or fragment")
        return self


class ClusterConfig(StrictModel):
    workers: list[Worker] = Field(min_length=1)
    profiles: dict[str, WorkflowProfile]
    foreground_cutout: Literal["alpha_or_mask", "rembg"] = "alpha_or_mask"
    font_file: str | None = None
    poll_interval_seconds: float = Field(default=2, gt=0, le=60)
    task_timeout_seconds: float = Field(default=1800, ge=1, le=86400)
    max_artifact_mb: int = Field(default=256, ge=1, le=2048)

    @model_validator(mode="after")
    def validate_workers(self):
        ids = [w.id for w in self.workers]
        if len(set(ids)) != len(ids):
            raise ValueError("worker ids must be unique")
        for role in ("background", "foreground"):
            if role not in self.profiles or not any(role in w.roles for w in self.workers):
                raise ValueError(f"missing profile or worker for {role}")
        return self
