"""N-worker pool, durable prompt IDs, and a required final composition barrier."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path

from filelock import FileLock, Timeout

from .comfy import ComfyClient, ComfyError, SubmissionUncertain
from .compositor import add_typography, compose, overlay_video, typography_layer
from .models import ClusterConfig, CreativePlan, PromptSpec, Worker, WorkflowProfile


class RunError(RuntimeError):
    pass


def load_workflow(profile: WorkflowProfile, config_dir: Path) -> dict:
    graph = json.loads((config_dir / profile.file).read_text(encoding="utf-8"))
    if not isinstance(graph, dict) or not graph or "nodes" in graph:
        raise RunError("Export workflow in ComfyUI API format, not editor format")
    for node in graph.values():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            raise RunError("Every workflow node must contain class_type and inputs")
    for node_id, input_name in profile.bindings.values():
        if node_id not in graph or input_name not in graph[node_id]["inputs"]:
            raise RunError("Workflow binding references a missing node/input")
    for selector in profile.outputs.values():
        if selector.node not in graph:
            raise RunError("Workflow output selector references a missing node")
    return graph


def bind_workflow(graph: dict, profile: WorkflowProfile, values: dict) -> dict:
    graph = copy.deepcopy(graph)
    data = {**profile.defaults, **values}
    for name, (node_id, input_name) in profile.bindings.items():
        if name not in data:
            raise RunError(f"No value/default for workflow binding: {name}")
        graph[node_id]["inputs"][input_name] = data[name]
    return graph


class Runner:
    def __init__(
        self,
        config: ClusterConfig,
        config_dir: Path,
        *,
        client_factory: Callable[[Worker], ComfyClient] | None = None,
    ):
        self.config = config
        self.config_dir = Path(config_dir)
        self.client_factory = client_factory or self._make_client
        self.clients: dict[str, ComfyClient] = {}
        self.workers = {w.id: w for w in config.workers}
        self.semaphores = {w.id: asyncio.Semaphore(w.capacity) for w in config.workers}
        self.queued = dict.fromkeys(self.workers, 0)
        self.eligible: dict[str, list[Worker]] = {}
        self.graphs = {role: load_workflow(p, self.config_dir) for role, p in config.profiles.items()}

    def _make_client(self, worker: Worker) -> ComfyClient:
        key = os.environ.get(worker.api_key_env) if worker.api_key_env else None
        if worker.api_key_env and not key:
            raise RunError(f"Worker auth environment variable is not set: {worker.api_key_env}")
        return ComfyClient(worker.url, api_key=key, max_artifact_mb=self.config.max_artifact_mb)

    def _save(self):
        temporary = self.run_dir / "run.json.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.run_dir / "run.json")

    def _artifact(self, relative: str) -> Path:
        path = (self.run_dir / relative).resolve()
        if not path.is_relative_to(self.run_dir.resolve()):
            raise RunError("Journal artifact path escapes run directory")
        return path

    async def _preflight(self, plan: CreativePlan):
        roles = ["background", "foreground"] + (["video"] if plan.video else [])
        if self.config.foreground_cutout == "rembg" and importlib.util.find_spec("rembg") is None:
            raise RunError("Install the cutout extra or configure a foreground workflow with alpha/mask")
        self.font = (self.config_dir / self.config.font_file) if self.config.font_file else None
        self.overlay = typography_layer(plan, self.font)
        if plan.video and plan.text_layers and not all(shutil.which(p) for p in ("ffmpeg", "ffprobe")):
            raise RunError("Video text overlay requires ffmpeg and ffprobe before GPU tasks start")
        # Probe independently. One offline machine need not block other compatible machines.
        health = await asyncio.gather(*(c.health() for c in self.clients.values()), return_exceptions=True)
        healthy = {key for key, result in zip(self.clients, health, strict=True) if not isinstance(result, BaseException)}
        for role in roles:
            if role not in self.graphs:
                raise RunError(f"Missing workflow profile: {role}")
            profile = self.config.profiles[role]
            if role == "video" and any(node["class_type"] == "Wan22ImageToVideoLatent" for node in self.graphs[role].values()):
                if plan.canvas.width % 32 or plan.canvas.height % 32 or (plan.video.frames - 1) % 4:
                    raise RunError("Wan2.2 profile requires canvas dimensions divisible by 32 and frames=4n+1")
            expected_output = "video" if role == "video" else "image"
            if expected_output not in profile.outputs:
                raise RunError(f"Profile {role} must declare an output named {expected_output}")
            if "prompt" not in profile.bindings or (role == "video" and "image" not in profile.bindings):
                raise RunError(f"Profile {role} must bind prompt and, for video, image")
            # Verify binding names/defaults before submitting anything.
            bind_workflow(self.graphs[role], profile, {
                "prompt": "preflight", "negative": "", "width": plan.canvas.width,
                "height": plan.canvas.height, "seed": 1, "image": "preflight.png",
                "frames": plan.video.frames if plan.video else 1,
                "fps": plan.video.fps if plan.video else 1,
                "duration_seconds": plan.video.duration_seconds if plan.video else 1,
            })
            candidates = [w for w in self.config.workers if role in w.roles and w.id in healthy]
            checks = await asyncio.gather(
                *(self.clients[w.id].validate_workflow(self.graphs[role]) for w in candidates),
                return_exceptions=True,
            )
            self.eligible[role] = [w for w, result in zip(candidates, checks, strict=True) if not isinstance(result, BaseException)]
            if not self.eligible[role]:
                raise RunError(f"No healthy worker with required node classes for role: {role}")

    async def _generate(
        self, task_id: str, role: str, prompt: PromptSpec, plan: CreativePlan, input_image: Path | None = None,
    ) -> dict[str, Path]:
        record = self.state["tasks"].setdefault(task_id, {"state": "pending", "seed": secrets.randbelow(2**53)})
        if record["state"] == "completed":
            paths = {name: self._artifact(path) for name, path in record["outputs"].items()}
            if all(path.is_file() for path in paths.values()):
                return paths
        if record["state"] in ("submitting", "uncertain") and not record.get("prompt_id"):
            raise RunError(f"Task {task_id} has an unconfirmed submission; check the worker queue/history manually")
        if record["state"] == "rejected":
            raise RunError(f"Task {task_id} was rejected; fix the workflow and use a new run directory")
        if record.get("worker"):
            worker = self.workers[record["worker"]]
        else:
            worker = min(self.eligible[role], key=lambda w: self.queued[w.id] / w.capacity)
            record["worker"] = worker.id
        self.queued[worker.id] += 1
        self._save()
        try:
            async with self.semaphores[worker.id]:
                client = self.clients[worker.id]
                if not record.get("prompt_id"):
                    values = {
                        "prompt": prompt.positive_en, "negative": prompt.negative,
                        "width": plan.canvas.width, "height": plan.canvas.height, "seed": record["seed"],
                    }
                    if plan.video and role == "video":
                        values.update(frames=plan.video.frames, fps=plan.video.fps, duration_seconds=plan.video.duration_seconds)
                    if input_image is not None:
                        values["image"] = await client.upload(input_image)
                    workflow = bind_workflow(self.graphs[role], self.config.profiles[role], values)
                    record["state"] = "submitting"
                    self._save()
                    try:
                        prompt_id = await client.submit(workflow, client_id=f"{self.state['run_id']}-{task_id}")
                    except SubmissionUncertain:
                        record["state"] = "uncertain"
                        self._save()
                        raise
                    except ComfyError:
                        record["state"] = "rejected"
                        self._save()
                        raise
                    record.update(state="submitted", prompt_id=prompt_id)
                    self._save()
                entry = await client.wait(
                    record["prompt_id"], poll_interval=self.config.poll_interval_seconds,
                    timeout=self.config.task_timeout_seconds,
                )
                outputs: dict[str, Path] = {}
                for name, selector in self.config.profiles[role].outputs.items():
                    if not name.isidentifier():
                        raise RunError("Output names must be Python-style identifiers")
                    try:
                        descriptor = entry["outputs"][selector.node][selector.field][selector.index]
                        suffix = Path(descriptor["filename"]).suffix.lower()
                    except (KeyError, IndexError, TypeError) as exc:
                        raise RunError(f"Task {task_id} is missing its configured {name} artifact") from exc
                    expected = {".mp4", ".webm", ".gif", ".mkv"} if name == "video" else {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
                    if suffix not in expected:
                        raise RunError(f"Task {task_id} returned an unexpected artifact format for {name}")
                    destination = self.run_dir / "artifacts" / f"{task_id}-{name}{suffix}"
                    outputs[name] = await client.download(descriptor, destination)
                record.update(state="completed", outputs={name: str(path.relative_to(self.run_dir)) for name, path in outputs.items()})
                self._save()
                return outputs
        finally:
            self.queued[worker.id] -= 1

    async def run(self, plan: CreativePlan, run_dir: Path) -> dict:
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.run_dir / ".lock")
        try:
            lock.acquire(timeout=0)
        except Timeout:
            raise RunError("This run directory is already in use") from None
        try:
            payload = {"plan": plan.model_dump(), "config": self.config.model_dump(), "workflows": self.graphs}
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            journal = self.run_dir / "run.json"
            self.state = json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else {
                "run_id": uuid.uuid4().hex, "fingerprint": fingerprint, "state": "pending", "tasks": {},
            }
            if self.state.get("fingerprint") != fingerprint:
                raise RunError("Plan, configuration, or workflow changed; use a new run directory")
            (self.run_dir / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
            self._save()
            async with AsyncExitStack() as stack:
                for worker in self.config.workers:
                    self.clients[worker.id] = await stack.enter_async_context(self.client_factory(worker))
                await self._preflight(plan)
                self.state["state"] = "running"
                self._save()
                jobs = [self._generate("background", "background", plan.background, plan),
                        self._generate("foreground", "foreground", plan.foreground, plan)]
                jobs.extend(self._generate(layer.id, "foreground", layer.prompt, plan) for layer in plan.extra_layers)
                results = await asyncio.gather(*jobs, return_exceptions=True)
                if any(isinstance(result, BaseException) for result in results):
                    self.state["state"] = "needs_attention"
                    self._save()
                    first = next(result for result in results if isinstance(result, BaseException))
                    raise RunError(f"Generation stopped before composition: {first}") from first
                background, foreground, *extras = results
                scene = self.run_dir / "scene.png"
                await asyncio.to_thread(compose, plan, background["image"], foreground["image"], scene,
                                        mask=foreground.get("mask"), cutout=self.config.foreground_cutout)
                for layer, images in zip(plan.extra_layers, extras, strict=True):
                    layer_plan = plan.model_copy(update={"placement": layer.placement})
                    await asyncio.to_thread(compose, layer_plan, scene, images["image"], scene,
                                            mask=images.get("mask"), cutout=self.config.foreground_cutout)
                final_image = self.run_dir / "final.png"
                await asyncio.to_thread(add_typography, scene, self.overlay, final_image)
                self.state["tasks"]["compose"] = {"state": "completed", "outputs": {"image": "final.png"}}
                self.state["final_image"] = "final.png"
                self._save()
                if plan.video:
                    video_prompt = PromptSpec(
                        positive_zh=plan.video.positive_zh, positive_en=plan.video.positive_en,
                        negative=plan.video.negative, acceptance=plan.video.acceptance,
                    )
                    video = (await self._generate("video", "video", video_prompt, plan, scene))["video"]
                    if plan.text_layers:
                        final_video = self.run_dir / "final.mp4"
                        await asyncio.to_thread(overlay_video, video, self.overlay, final_video)
                    else:
                        final_video = self.run_dir / ("final" + video.suffix)
                        shutil.copyfile(video, final_video)
                    self.state["final_video"] = str(final_video.relative_to(self.run_dir))
                self.state["state"] = "completed"
                self._save()
                return copy.deepcopy(self.state)
        except BaseException:
            if hasattr(self, "state") and self.state.get("state") == "running":
                self.state["state"] = "needs_attention"
                self._save()
            raise
        finally:
            lock.release()
