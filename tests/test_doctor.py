import json
from pathlib import Path

import httpx

from comfyui_director.cli import main
from comfyui_director.comfy import ComfyClient
from comfyui_director.doctor import diagnose
from comfyui_director.models import ClusterConfig, CreativePlan


def fixture_config(tmp_path: Path):
    repository = Path(__file__).resolve().parents[1]
    data = json.loads((repository / "cluster.example.json").read_text())
    data["foreground_cutout"] = "alpha_or_mask"
    data["profiles"].pop("video")
    for profile in data["profiles"].values():
        profile["file"] = str(repository / profile["file"])
        profile["defaults"]["checkpoint"] = "director-custom.safetensors"
    data["workers"] = [
        {"id": "missing", "url": "http://missing", "roles": ["background", "foreground"]},
        {"id": "ready", "url": "http://ready", "roles": ["background", "foreground"]},
        {"id": "auth", "url": "http://auth", "roles": ["background"], "api_key_env": "DIRECTOR_TEST_MISSING_AUTH"},
        {"id": "video", "url": "http://video", "roles": ["video"], "api_key_env": "DIRECTOR_TEST_UNUSED_AUTH"},
    ]
    config_path = tmp_path / "cluster.json"
    config_path.write_text(json.dumps(data))
    plan_data = json.loads((repository / "examples/poster-plan.json").read_text())
    plan_data["text_layers"] = []
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_data, ensure_ascii=False))
    return config_path, plan_path


def simulate_workers(monkeypatch, *, ready=True):
    requests = []

    def handle(request):
        host, path = request.url.host, request.url.path
        requests.append((host, path))
        assert request.method == "GET"
        assert host in {"ready", "missing"}
        if path == "/system_stats":
            return httpx.Response(200, json={"devices": []})
        assert path == "/object_info"
        checkpoint = "director-custom.safetensors" if host == "ready" and ready else "other-model.safetensors"
        info = dict.fromkeys(("VAEDecode", "EmptyLatentImage", "CLIPTextEncode", "KSampler", "SaveImage"), {})
        info["CheckpointLoaderSimple"] = {"input": {"required": {"ckpt_name": [[checkpoint]]}}}
        return httpx.Response(200, json=info)

    def make_client(worker, size):
        if worker.api_key_env:
            from comfyui_director.diagnostics import create_worker_client

            return create_worker_client(worker, size)
        return ComfyClient(worker.url, transport=httpx.MockTransport(handle))

    monkeypatch.delenv("DIRECTOR_TEST_MISSING_AUTH", raising=False)
    monkeypatch.setattr("comfyui_director.doctor.create_worker_client", make_client)
    return requests


def test_doctor_checks_bound_model_with_no_submission_and_isolates_unused_workers(tmp_path, monkeypatch, capsys):
    config, plan = fixture_config(tmp_path)
    requests = simulate_workers(monkeypatch)
    assert main(["doctor", "--config", str(config), "--plan", str(plan)]) == 0
    output = capsys.readouterr().out
    assert "通过 ready / background" in output
    assert "缺项 missing / foreground" in output
    assert "director-custom.safetensors" in output
    assert "缺项 auth / background" in output
    assert "video" not in output
    assert sorted(requests) == [
        ("missing", "/object_info"), ("missing", "/system_stats"),
        ("ready", "/object_info"), ("ready", "/system_stats"),
    ]


async def test_doctor_reports_required_roles_with_no_available_workers(tmp_path, monkeypatch):
    config, plan = fixture_config(tmp_path)
    simulate_workers(monkeypatch, ready=False)
    report = await diagnose(
        ClusterConfig.model_validate_json(config.read_text()), tmp_path,
        plan=CreativePlan.model_validate_json(plan.read_text()),
    )
    assert not report.ready
    assert {check.role for check in report.checks} == {"background", "foreground"}
    assert all(not check.ok for check in report.checks)
