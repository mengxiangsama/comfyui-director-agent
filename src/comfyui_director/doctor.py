"""Read-only configuration and worker diagnostics for the command line."""

import importlib.util
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

from .comfy import ComfyClient, ComfyError
from .compositor import typography_layer
from .diagnostics import WorkerCheck, create_worker_client, probe_workers
from .models import ClusterConfig, CreativePlan
from .runner import RunError, bind_workflow, load_workflow


@dataclass(frozen=True)
class DoctorReport:
    roles: set[str]
    checks: list[WorkerCheck]
    errors: list[str]
    notes: list[str]

    @property
    def ready(self) -> bool:
        ready_roles = {check.role for check in self.checks if check.ok}
        return not self.errors and self.roles <= ready_roles


async def diagnose(
    config: ClusterConfig, config_dir: Path, *, plan: CreativePlan | None = None,
) -> DoctorReport:
    roles = {"background", "foreground"}
    if (plan is not None and plan.video is not None) or (
        plan is None and ("video" in config.profiles or any("video" in w.roles for w in config.workers))
    ):
        roles.add("video")

    errors = []
    notes = []
    if config.foreground_cutout == "rembg":
        if importlib.util.find_spec("rembg") is None:
            errors.append("缺少 rembg。请安装 [cutout] 依赖，或使用提供 alpha/遮罩的前景工作流。")
        else:
            notes.append("已找到 rembg，首次抠图仍需可用的分割模型缓存或下载。")

    font = config_dir / config.font_file if config.font_file else None
    if plan and plan.text_layers:
        try:
            typography_layer(plan, font)
        except (ValueError, OSError) as exc:
            errors.append(f"文字排版检查失败：{exc}")
    elif plan is None and font is not None and not font.is_file():
        notes.append("配置的字体文件不存在，有文字图层的计划需要可用字体。")
    if "video" in roles and not all(shutil.which(name) for name in ("ffmpeg", "ffprobe")):
        message = "带文字的视频需要在 PATH 中安装 ffmpeg 和 ffprobe。"
        if plan and plan.text_layers:
            errors.append(message)
        elif plan is None:
            notes.append(message)

    workflows = {}
    for role in sorted(roles):
        try:
            profile = config.profiles.get(role)
            if profile is None:
                raise RunError("Missing workflow profile")
            graph = load_workflow(profile, config_dir)
            output = "video" if role == "video" else "image"
            if output not in profile.outputs:
                raise RunError(f"Workflow profile has no {output} output selector")
            if "prompt" not in profile.bindings or (role == "video" and "image" not in profile.bindings):
                raise RunError("Workflow profile must bind prompt and, for video, image")
            # Without a plan, retain template values for known runtime parameters.
            runtime_keys = {"prompt", "negative", "width", "height", "seed", "image", "frames", "fps", "duration_seconds"}
            values = {
                name: graph[node_id]["inputs"][input_name]
                for name, (node_id, input_name) in profile.bindings.items() if name in runtime_keys
            }
            if plan:
                prompt = plan.background if role == "background" else plan.foreground
                values.update(prompt=prompt.positive_en, negative=prompt.negative, width=plan.canvas.width, height=plan.canvas.height, seed=1)
                if role == "video" and plan.video:
                    video = plan.video
                    values.update(prompt=video.positive_en, negative=video.negative, frames=video.frames, fps=video.fps, duration_seconds=video.duration_seconds)
                    if any(node["class_type"] == "Wan22ImageToVideoLatent" for node in graph.values()):
                        if plan.canvas.width % 32 or plan.canvas.height % 32 or (video.frames - 1) % 4:
                            raise RunError("Wan2.2 requires canvas dimensions divisible by 32 and frames=4n+1")
            workflows[role] = bind_workflow(graph, profile, values)
        except (RunError, OSError, ValueError, TypeError) as exc:
            errors.append(f"{role} 工作流配置有误：{exc}")

    clients: dict[str, ComfyClient] = {}
    unavailable = {}
    async with AsyncExitStack() as stack:
        for worker in config.workers:
            if not roles.intersection(worker.roles):
                continue
            try:
                client = create_worker_client(worker, config.max_artifact_mb)
                clients[worker.id] = await stack.enter_async_context(client)
            except (ComfyError, ValueError) as exc:
                unavailable[worker.id] = str(exc)
        checks = await probe_workers(config, clients, workflows, roles=roles, unavailable=unavailable)
    return DoctorReport(roles, checks, errors, notes)
