import asyncio
import io
import json
import threading
from collections import Counter

import httpx
import pytest
from filelock import FileLock, Timeout
from PIL import Image

from comfyui_director.comfy import ComfyClient
from comfyui_director.models import ClusterConfig, CreativePlan
from comfyui_director.runner import RunError, Runner


def prompt(name):
    return {"positive_zh": name, "positive_en": name, "acceptance": ["test"]}


def make_plan(*, extras=0, video=False, text=False):
    data = {
        "title": "integration test",
        "request": "test",
        "medium": "video" if video else "image",
        "canvas": {"width": 64, "height": 64},
        "visual": {
            "style": "test",
            "palette": "test",
            "lighting": "test",
            "camera": "test",
            "composition": "test",
        },
        "full_image_prompt": prompt("full"),
        "background": prompt("background"),
        "foreground": prompt("foreground"),
        "placement": {
            "x": 16,
            "y": 16,
            "width": 32,
            "height": 32,
            "instructions": "center",
            "acceptance": ["test"],
        },
        "extra_layers": [
            {
                "id": f"extra_{index}",
                "prompt": prompt(f"extra_{index}"),
                "placement": {
                    "x": 24,
                    "y": 24,
                    "width": 16,
                    "height": 16,
                    "instructions": "overlap",
                    "acceptance": ["test"],
                },
            }
            for index in range(extras)
        ],
        "text_layers": [{"text": "A", "x": 0, "y": 0, "font_size": 8}] if text else [],
    }
    if video:
        data["video"] = {
            "positive_zh": "video",
            "positive_en": "video",
            "frames": 81,
            "fps": 16,
            "duration_seconds": 5,
            "acceptance": ["test"],
        }
    return CreativePlan.model_validate(data)


def make_config(tmp_path, *, capacities=(1, 1, 1), video=False):
    workflow = {
        "1": {
            "class_type": "GenerateImage",
            "inputs": {"text": "", "width": 64, "height": 64, "seed": 0},
        },
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    (tmp_path / "image.json").write_text(json.dumps(workflow))
    bindings = {
        "prompt": ["1", "text"],
        "width": ["1", "width"],
        "height": ["1", "height"],
        "seed": ["1", "seed"],
    }
    image_profile = {
        "file": "image.json",
        "bindings": bindings,
        "outputs": {"image": {"node": "2", "field": "images"}},
    }
    profiles = {"background": image_profile, "foreground": image_profile}
    if video:
        workflow["1"]["inputs"]["image"] = ""
        (tmp_path / "video.json").write_text(json.dumps(workflow))
        profiles["video"] = {
            "file": "video.json",
            "bindings": {**bindings, "image": ["1", "image"]},
            "outputs": {"video": {"node": "2", "field": "videos"}},
        }
    return ClusterConfig.model_validate(
        {
            "workers": [
                {
                    "id": f"w{index}",
                    "url": f"http://worker-{index}",
                    "roles": ["background", "foreground"] + (["video"] if video else []),
                    "capacity": capacity,
                }
                for index, capacity in enumerate(capacities)
            ],
            "profiles": profiles,
            "poll_interval_seconds": 0.001,
            "task_timeout_seconds": 1,
        }
    )


def png(color, *, alpha=False):
    image = Image.new("RGBA" if alpha else "RGB", (64, 64), (0, 0, 0, 0) if alpha else color)
    if alpha:
        for y in range(16, 48):
            for x in range(16, 48):
                image.putpixel((x, y), color)
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


class WorkerPool:
    """Protocol-level worker doubles; Runner still uses the real ComfyClient."""

    def __init__(self, *, offline=(), execution_error=None, uncertain=None, delay=0.003):
        self.offline = set(offline)
        self.execution_error = execution_error
        self.uncertain = uncertain
        self.delay = delay
        self.submissions = []
        self.tasks = {}
        self.artifacts = {}
        self.active = Counter()
        self.max_active = Counter()
        self.max_total = 0
        self.completed = set()
        self.events = []
        self.uploads = []

    def client(self, worker):
        return ComfyClient(worker.url, transport=httpx.MockTransport(self.handle))

    async def handle(self, request):
        host, path = request.url.host, request.url.path
        if host in self.offline:
            return httpx.Response(503, text="offline")
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {"python_version": "test"}})
        if path == "/object_info":
            return httpx.Response(200, json={"GenerateImage": {}, "SaveImage": {}})
        if path == "/prompt":
            payload = json.loads(request.content)
            task = payload["prompt"]["1"]["inputs"]["text"]
            prompt_id = f"p-{len(self.submissions)}"
            self.submissions.append((host, task, prompt_id))
            self.tasks[prompt_id] = (host, task)
            self.events.append(("submit", task))
            self.active[host] += 1
            self.max_active[host] = max(self.max_active[host], self.active[host])
            self.max_total = max(self.max_total, sum(self.active.values()))
            if self.uncertain == task:
                raise httpx.ReadTimeout("lost acknowledgement", request=request)
            return httpx.Response(200, json={"prompt_id": prompt_id})
        if path.startswith("/history/"):
            prompt_id = path.rsplit("/", 1)[1]
            task_host, task = self.tasks[prompt_id]
            assert task_host == host
            await asyncio.sleep(self.delay)
            if prompt_id not in self.completed:
                self.completed.add(prompt_id)
                self.active[host] -= 1
                self.events.append(("complete", task))
            if task == self.execution_error:
                return httpx.Response(
                    200,
                    json={
                        prompt_id: {
                            "status": {"status_str": "error", "messages": [["execution_error", {}]]}
                        }
                    },
                )
            filename = f"{prompt_id}.mp4" if task == "video" else f"{prompt_id}.png"
            if task == "video":
                content, field = b"fake video bytes", "videos"
            elif task == "background":
                content, field = png("blue"), "images"
            else:
                color = (255, 0, 0, 255) if task == "foreground" else (0, 255, 0, 255)
                if task == "extra_1":
                    color = (255, 255, 0, 255)
                content, field = png(color, alpha=True), "images"
            self.artifacts[filename] = content
            return httpx.Response(
                200,
                json={
                    prompt_id: {
                        "status": {"completed": True},
                        "outputs": {
                            "2": {
                                field: [{"filename": filename, "type": "output", "subfolder": ""}]
                            }
                        },
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=self.artifacts[request.url.params["filename"]])
        if path == "/upload/image":
            self.uploads.append(request.content)
            self.events.append(("upload", "video"))
            return httpx.Response(200, json={"name": "scene.png", "subfolder": "test"})
        raise AssertionError(f"Unexpected worker request: {request.method} {path}")


class RecordingPool(WorkerPool):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.requests = []
        self.payloads = []
        self.failure_tasks = set()
        self.reject_tasks = set()
        self.download_failures = set()
        self.history_pending = set()
        self.node_infos = {}

    async def handle(self, request):
        self.requests.append((request.url.host, request.method, request.url.path))
        if request.url.path == "/prompt":
            body = json.loads(request.content)
            self.payloads.append(body)
            task = body["prompt"]["1"]["inputs"]["text"]
            if task in self.reject_tasks:
                return httpx.Response(400, json={"error": "private remote detail"})
        if request.url.path == "/object_info" and request.url.host in self.node_infos:
            return httpx.Response(200, json=self.node_infos[request.url.host])
        if request.url.path.startswith("/history/"):
            prompt_id = request.url.path.rsplit("/", 1)[1]
            if self.tasks[prompt_id][1] in self.history_pending:
                return httpx.Response(200, json={})
        if request.url.path == "/view":
            prompt_id = request.url.params["filename"].rsplit(".", 1)[0]
            if self.tasks[prompt_id][1] in self.download_failures:
                return httpx.Response(503, text="private remote detail")
        response = await super().handle(request)
        if request.url.path.startswith("/history/"):
            prompt_id = request.url.path.rsplit("/", 1)[1]
            if self.tasks[prompt_id][1] in self.failure_tasks:
                return httpx.Response(
                    200,
                    json={
                        prompt_id: {
                            "status": {
                                "status_str": "error",
                                "messages": [
                                    ["execution_error", {"detail": "private remote detail"}]
                                ],
                            }
                        }
                    },
                )
        return response


def journal(run_dir):
    return json.loads((run_dir / "run.json").read_text())


async def test_three_workers_parallel_layers_and_final_composition_barrier(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool()
    plan = make_plan(extras=2)
    run_dir = tmp_path / "run"
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert state["state"] == "completed"
    assert len({host for host, _, _ in pool.submissions}) == 3
    assert pool.max_total == 3
    assert all(value == 1 for value in pool.max_active.values())
    assert set(state["tasks"]) == {"background", "foreground", "extra_0", "extra_1", "compose"}
    assert all(task["state"] == "completed" for task in state["tasks"].values())
    assert CreativePlan.model_validate_json((run_dir / "plan.json").read_text()) == plan
    with Image.open(run_dir / "final.png") as image:
        assert image.size == (64, 64)
        assert image.getpixel((0, 0)) == (0, 0, 255)
        assert image.getpixel((18, 18)) == (255, 0, 0)
        # Later extra layers cover earlier ones, regardless of completion ordering.
        assert image.getpixel((30, 30)) == (255, 255, 0)


async def test_single_worker_capacity_is_respected_for_multiple_layers(tmp_path):
    config = make_config(tmp_path, capacities=(2,))
    pool = WorkerPool(delay=0.005)
    await Runner(config, tmp_path, client_factory=pool.client).run(
        make_plan(extras=4), tmp_path / "run"
    )
    assert pool.max_active["worker-0"] == 2
    assert len(pool.submissions) == 6


async def test_offline_worker_does_not_block_healthy_pool(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool(offline={"worker-0"})
    state = await Runner(config, tmp_path, client_factory=pool.client).run(
        make_plan(extras=1), tmp_path / "run"
    )
    assert state["state"] == "completed"
    assert {host for host, _, _ in pool.submissions} == {"worker-1", "worker-2"}


async def test_execution_failure_stops_before_any_final_composition(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool(execution_error="extra_0")
    run_dir = tmp_path / "run"
    with pytest.raises(RunError, match="before composition"):
        await Runner(config, tmp_path, client_factory=pool.client).run(make_plan(extras=1), run_dir)
    assert not (run_dir / "scene.png").exists()
    assert not (run_dir / "final.png").exists()
    journal = json.loads((run_dir / "run.json").read_text())
    assert journal["state"] == "needs_attention"
    assert "compose" not in journal["tasks"]


async def test_completed_run_resumes_without_resubmitting_gpu_jobs(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool()
    plan, run_dir = make_plan(), tmp_path / "run"
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    first_count = len(pool.submissions)
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert len(pool.submissions) == first_count == 2


async def test_known_prompt_id_is_recovered_without_new_submission(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool()
    plan, run_dir = make_plan(), tmp_path / "run"
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    journal_path = run_dir / "run.json"
    journal = json.loads(journal_path.read_text())
    journal["state"] = "needs_attention"
    record = journal["tasks"]["foreground"]
    record["state"] = "submitted"
    old_prompt_id = record["prompt_id"]
    (run_dir / record["outputs"]["image"]).unlink()
    record.pop("outputs")
    journal_path.write_text(json.dumps(journal))
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert state["tasks"]["foreground"]["prompt_id"] == old_prompt_id
    assert len(pool.submissions) == 2
    assert (run_dir / "final.png").exists()


async def test_uncertain_submission_is_never_repeated_on_resume(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool(uncertain="foreground")
    plan, run_dir = make_plan(), tmp_path / "run"
    with pytest.raises(RunError, match="before composition"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    with pytest.raises(RunError, match="unconfirmed submission"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert Counter(task for _, task, _ in pool.submissions)["foreground"] == 1
    assert not (run_dir / "final.png").exists()
    journal = json.loads((run_dir / "run.json").read_text())
    assert journal["tasks"]["foreground"]["state"] == "uncertain"


@pytest.mark.parametrize("change", ["plan", "config", "workflow"])
async def test_changed_plan_config_or_workflow_rejects_old_journal(tmp_path, change):
    config = make_config(tmp_path)
    pool = WorkerPool()
    plan, run_dir = make_plan(), tmp_path / "run"
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    if change == "plan":
        plan = plan.model_copy(update={"title": "changed"})
    elif change == "config":
        config = config.model_copy(update={"poll_interval_seconds": 0.002})
    else:
        workflow = json.loads((tmp_path / "image.json").read_text())
        workflow["1"]["inputs"]["width"] = 128
        (tmp_path / "image.json").write_text(json.dumps(workflow))
    with pytest.raises(RunError, match="changed"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert len(pool.submissions) == 2


async def test_video_uses_composited_scene_and_text_is_added_after_generation(
    tmp_path, monkeypatch
):
    config = make_config(tmp_path, video=True)
    pool = WorkerPool()
    run_dir = tmp_path / "run"
    plan = make_plan(video=True, text=True)
    overlay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    overlay.putpixel((0, 0), (255, 0, 255, 255))
    monkeypatch.setattr("comfyui_director.runner.typography_layer", lambda *args: overlay)
    monkeypatch.setattr("comfyui_director.runner.shutil.which", lambda name: name)

    def fake_overlay_video(source, layer, destination):
        pool.events.append(("text-overlay", "video"))
        assert ("complete", "video") in pool.events
        assert layer.getpixel((0, 0)) == (255, 0, 255, 255)
        destination.write_bytes(source.read_bytes() + b" plus exact text overlay")
        return destination

    monkeypatch.setattr("comfyui_director.runner.overlay_video", fake_overlay_video)
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    video_submit = pool.events.index(("submit", "video"))
    assert pool.events.index(("complete", "background")) < video_submit
    assert pool.events.index(("complete", "foreground")) < video_submit
    assert pool.events.index(("upload", "video")) < video_submit
    assert pool.events.index(("complete", "video")) < pool.events.index(("text-overlay", "video"))
    assert (run_dir / "scene.png").read_bytes() in pool.uploads[0]
    assert (run_dir / "final.png").read_bytes() not in pool.uploads[0]
    with Image.open(run_dir / "final.png") as image:
        assert image.getpixel((0, 0)) == (255, 0, 255)
    assert state["final_video"] == "final.mp4"
    assert (run_dir / "final.mp4").read_bytes().endswith(b" plus exact text overlay")


async def test_missing_font_is_caught_before_gpu_submission(tmp_path):
    config = make_config(tmp_path)
    pool = WorkerPool()
    with pytest.raises(ValueError, match="font_file"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            make_plan(text=True), tmp_path / "run"
        )
    assert pool.submissions == []


@pytest.mark.parametrize("width,height,frames", [(80, 64, 81), (64, 80, 81), (64, 64, 80)])
async def test_wan_incompatible_dimensions_or_frames_fail_before_gpu_tasks(
    tmp_path, width, height, frames
):
    config = make_config(tmp_path, video=True)
    graph_path = tmp_path / "video.json"
    graph = json.loads(graph_path.read_text())
    graph["3"] = {"class_type": "Wan22ImageToVideoLatent", "inputs": {}}
    graph_path.write_text(json.dumps(graph))
    payload = make_plan(video=True).model_dump()
    payload["canvas"] = {"width": width, "height": height}
    payload["video"]["frames"] = frames
    payload["video"]["duration_seconds"] = frames / payload["video"]["fps"]
    plan = CreativePlan.model_validate(payload)
    pool = WorkerPool()
    with pytest.raises(RunError, match="divisible by 32 and frames=4n"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, tmp_path / "run")
    assert pool.submissions == []


async def test_confirmed_failure_has_terminal_state_and_explicit_single_task_retry(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(extras=1), RecordingPool()
    pool.failure_tasks.add("foreground")
    run_dir = tmp_path / "run"
    with pytest.raises(RunError, match="--retry foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    old = journal(run_dir)
    record = old["tasks"]["foreground"]
    assert record["state"] == "failed"
    assert record["error_code"] == "execution_failed"
    assert "private remote detail" not in json.dumps(old)
    count = len(pool.requests)
    with pytest.raises(RunError, match="--retry foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert len(pool.requests) == count
    pool.failure_tasks.clear()
    state = await Runner(config, tmp_path, client_factory=pool.client).run(
        plan,
        run_dir,
        retry_tasks=("foreground",),
    )
    current = state["tasks"]["foreground"]
    assert state["state"] == "completed"
    assert current["seed"] == record["seed"]
    assert current["prompt_id"] != record["prompt_id"]
    assert current["attempt"] == 2
    assert current["attempts"] == [
        {
            "state": "failed",
            "worker": record["worker"],
            "prompt_id": record["prompt_id"],
            "seed": record["seed"],
            "error_code": "execution_failed",
            "attempt": 1,
        }
    ]
    assert Counter(task for _, task, _ in pool.submissions) == {
        "background": 1,
        "foreground": 2,
        "extra_0": 1,
    }
    seeds = [
        item["prompt"]["1"]["inputs"]["seed"]
        for item in pool.payloads
        if item["prompt"]["1"]["inputs"]["text"] == "foreground"
    ]
    assert seeds == [record["seed"], record["seed"]]
    assert old["fingerprint"] == state["fingerprint"]


async def test_rejected_submission_can_be_explicitly_retried(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    pool.reject_tasks.add("foreground")
    run_dir = tmp_path / "run"
    with pytest.raises(RunError, match="--retry foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    rejected = journal(run_dir)["tasks"]["foreground"]
    assert rejected["state"] == "rejected"
    assert "prompt_id" not in rejected
    pool.reject_tasks.clear()
    state = await Runner(config, tmp_path, client_factory=pool.client).run(
        plan, run_dir, retry_tasks=("foreground",)
    )
    assert state["tasks"]["foreground"]["seed"] == rejected["seed"]
    assert state["tasks"]["foreground"]["attempts"][0]["state"] == "rejected"
    assert Counter(task for _, task, _ in pool.submissions) == {"background": 1, "foreground": 1}


async def test_two_failed_layers_can_be_retried_individually(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(extras=1), RecordingPool()
    pool.failure_tasks.update({"foreground", "extra_0"})
    run_dir = tmp_path / "run"
    with pytest.raises(RunError):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    extra_before = journal(run_dir)["tasks"]["extra_0"]
    pool.failure_tasks.clear()
    with pytest.raises(RunError, match="--retry extra_0"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=("foreground",)
        )
    partial = journal(run_dir)
    assert partial["tasks"]["foreground"]["state"] == "completed"
    assert partial["tasks"]["extra_0"] == extra_before
    assert not (run_dir / "final.png").exists()
    state = await Runner(config, tmp_path, client_factory=pool.client).run(
        plan, run_dir, retry_tasks=("extra_0",)
    )
    assert state["state"] == "completed"
    assert Counter(task for _, task, _ in pool.submissions) == {
        "background": 1,
        "foreground": 2,
        "extra_0": 2,
    }


@pytest.mark.parametrize(
    "state", ["completed", "submitted", "uncertain", "submitting", "downloading", "pending"]
)
async def test_retry_rejects_unconfirmed_or_successful_tasks_without_mutation(tmp_path, state):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    run_dir = tmp_path / "run"
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    data = journal(run_dir)
    data["tasks"]["foreground"]["state"] = state
    if state in ("uncertain", "submitting", "pending"):
        data["tasks"]["foreground"].pop("prompt_id", None)
    path = run_dir / "run.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    count = len(pool.requests)
    with pytest.raises(RunError, match="cannot be retried"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=("foreground",)
        )
    assert path.read_bytes() == before
    assert len(pool.requests) == count


@pytest.mark.parametrize("tasks", [("foreground", "unknown"), ("foreground", "foreground")])
async def test_retry_batch_validation_is_atomic(tmp_path, tasks):
    config, plan, pool = (
        make_config(tmp_path),
        make_plan(),
        RecordingPool(execution_error="foreground"),
    )
    run_dir = tmp_path / "run"
    with pytest.raises(RunError):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    path = run_dir / "run.json"
    before = path.read_bytes()
    with pytest.raises(RunError):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=tasks
        )
    assert path.read_bytes() == before
    assert len(pool.submissions) == 2


async def test_legacy_submitted_failure_is_confirmed_before_retry(tmp_path):
    config, plan, pool = (
        make_config(tmp_path),
        make_plan(),
        RecordingPool(execution_error="foreground"),
    )
    run_dir = tmp_path / "run"
    with pytest.raises(RunError):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    data = journal(run_dir)
    record = data["tasks"]["foreground"]
    record["state"] = "submitted"
    record.pop("error_code")
    record.pop("attempt")
    (run_dir / "run.json").write_text(json.dumps(data))
    with pytest.raises(RunError, match="cannot be retried"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=("foreground",)
        )
    with pytest.raises(RunError, match="--retry foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert journal(run_dir)["tasks"]["foreground"]["state"] == "failed"
    pool.execution_error = None
    state = await Runner(config, tmp_path, client_factory=pool.client).run(
        plan, run_dir, retry_tasks=("foreground",)
    )
    assert state["state"] == "completed"
    assert state["tasks"]["foreground"]["attempt"] == 2


async def test_poll_timeout_is_not_a_retryable_execution_failure(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    pool.history_pending.add("foreground")
    run_dir = tmp_path / "run"
    with pytest.raises(RunError, match="Task foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert journal(run_dir)["tasks"]["foreground"]["state"] == "submitted"
    with pytest.raises(RunError, match="cannot be retried"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=("foreground",)
        )
    pool.history_pending.clear()
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert len(pool.submissions) == 2


async def test_download_failure_resumes_same_prompt_without_rerender(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    pool.download_failures.add("foreground")
    run_dir = tmp_path / "run"
    with pytest.raises(RunError, match="Task foreground"):
        await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    record = journal(run_dir)["tasks"]["foreground"]
    assert record["state"] == "downloading"
    with pytest.raises(RunError, match="cannot be retried"):
        await Runner(config, tmp_path, client_factory=pool.client).run(
            plan, run_dir, retry_tasks=("foreground",)
        )
    pool.download_failures.clear()
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert state["tasks"]["foreground"]["prompt_id"] == record["prompt_id"]
    assert len(pool.submissions) == 2


@pytest.mark.parametrize("legacy,valid_image", [(False, False), (True, False), (False, True)])
async def test_corrupt_cached_image_is_redownloaded_using_original_prompt(
    tmp_path, legacy, valid_image
):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    run_dir = tmp_path / "run"
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    record = state["tasks"]["foreground"]
    path = run_dir / record["outputs"]["image"]
    original = path.read_bytes()
    path.write_bytes(png((0, 255, 0, 255), alpha=True) if valid_image else b"truncated PNG")
    if legacy:
        for item in state["tasks"].values():
            item.pop("artifact_integrity", None)
        (run_dir / "run.json").write_text(json.dumps(state))
    old_views = sum(url == "/view" for _, _, url in pool.requests)
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert path.read_bytes() == original
    assert state["tasks"]["foreground"]["prompt_id"] == record["prompt_id"]
    assert sum(url == "/view" for _, _, url in pool.requests) == old_views + 1
    assert len(pool.submissions) == 2


async def test_cancellation_holds_run_lock_until_local_writer_finishes(tmp_path, monkeypatch):
    import comfyui_director.runner as module

    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = module.compose

    def slow_compose(*args, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(module, "compose", slow_compose)
    run_dir = tmp_path / "run"
    task = asyncio.create_task(
        Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    )
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        with pytest.raises(Timeout):
            FileLock(run_dir / ".lock").acquire(timeout=0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    lock = FileLock(run_dir / ".lock")
    with lock.acquire(timeout=0):
        assert journal(run_dir)["state"] == "needs_attention"
    monkeypatch.setattr(module, "compose", original)
    assert (await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir))[
        "state"
    ] == "completed"
    assert len(pool.submissions) == 2


async def test_pending_task_can_choose_another_worker_before_submission(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    run_dir = tmp_path / "run"
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    record = state["tasks"]["foreground"]
    old_worker = record["worker"]
    record.update(state="pending")
    for key in ("prompt_id", "outputs", "artifact_integrity"):
        record.pop(key)
    (run_dir / "run.json").write_text(json.dumps(state))
    pool.offline.add(
        next(w.url.removeprefix("http://") for w in config.workers if w.id == old_worker)
    )
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert state["tasks"]["foreground"]["worker"] != old_worker


async def test_known_prompt_resume_does_not_require_current_model_capabilities(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    run_dir = tmp_path / "run"
    state = await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    record = state["tasks"]["foreground"]
    (run_dir / record["outputs"]["image"]).unlink()
    for worker in config.workers:
        pool.node_infos[worker.url.removeprefix("http://")] = {}
    pool.requests.clear()
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, run_dir)
    assert not any(
        path in ("/object_info", "/system_stats", "/prompt") for _, _, path in pool.requests
    )
    assert any(path == f"/history/{record['prompt_id']}" for _, _, path in pool.requests)


@pytest.mark.parametrize("error", ["credentials", "invalid_url"])
async def test_one_client_initialization_failure_does_not_block_other_workers(tmp_path, error):
    from comfyui_director.comfy import ComfyError

    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()

    def factory(worker):
        if worker.id == "w0":
            if error == "credentials":
                raise ComfyError("Worker auth environment variable is not set: TEST_KEY")
            raise ValueError("private invalid URL detail")
        return pool.client(worker)

    state = await Runner(config, tmp_path, client_factory=factory).run(plan, tmp_path / "run")
    assert state["state"] == "completed"
    assert "worker-0" not in {host for host, _, _ in pool.submissions}


async def test_unused_video_worker_is_not_initialized_for_image_plan(tmp_path):
    config, plan, pool = make_config(tmp_path, video=True), make_plan(), RecordingPool()
    payload = config.model_dump()
    payload["workers"].append({"id": "video_only", "url": "http://video-only", "roles": ["video"]})
    config = ClusterConfig.model_validate(payload)

    def factory(worker):
        assert worker.id != "video_only"
        return pool.client(worker)

    await Runner(config, tmp_path, client_factory=factory).run(plan, tmp_path / "run")


async def test_static_model_diagnostics_use_bound_profile_defaults(tmp_path):
    config, plan, pool = make_config(tmp_path), make_plan(), RecordingPool()
    graph_path = tmp_path / "image.json"
    graph = json.loads(graph_path.read_text())
    graph["1"]["inputs"]["ckpt_name"] = "placeholder"
    graph_path.write_text(json.dumps(graph))
    for profile in config.profiles.values():
        profile.bindings["checkpoint"] = ("1", "ckpt_name")
        profile.defaults["checkpoint"] = "correct.safetensors"
    for index in range(3):
        pool.node_infos[f"worker-{index}"] = {
            "GenerateImage": {
                "input": {
                    "required": {
                        "ckpt_name": [
                            ["missing.safetensors" if index == 0 else "correct.safetensors"]
                        ]
                    }
                }
            },
            "SaveImage": {},
        }
    await Runner(config, tmp_path, client_factory=pool.client).run(plan, tmp_path / "run")
    assert "worker-0" not in {host for host, _, _ in pool.submissions}
