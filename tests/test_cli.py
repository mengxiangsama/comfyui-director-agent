import json
from importlib.resources import files

import pytest
from PIL import Image, ImageChops

from comfyui_director import cli
from comfyui_director.models import ClusterConfig, CreativePlan
from comfyui_director.runner import bind_workflow, load_workflow


@pytest.mark.parametrize("medium", ["image", "video", "multi"])
def test_demo_exports_valid_offline_plan_and_complete_handoff(tmp_path, medium, monkeypatch, capsys):
    async def forbidden(*args, **kwargs):
        pytest.fail("offline demo must never call a language model or GPU")

    monkeypatch.setattr(cli, "generate_plan", forbidden)
    monkeypatch.setattr(cli.Runner, "run", forbidden)
    assert cli.main(["demo", "--medium", medium, "--out-dir", str(tmp_path)]) == 0
    plan_path = next(tmp_path.glob("*.json"))
    plan = CreativePlan.model_validate_json(plan_path.read_text())
    report = plan_path.with_suffix(".md").read_text()
    assert plan.foreground.positive_en in report
    assert plan.background.positive_en in report
    assert "合成" in report
    assert "未调用 LLM/GPU" in capsys.readouterr().out
    if medium == "multi":
        assert len(plan.extra_layers) >= 2


def test_plan_calls_provider_then_exports_validated_json_and_markdown(tmp_path, monkeypatch):
    sample = CreativePlan.model_validate_json(files("comfyui_director.data").joinpath("poster-plan.json").read_text())
    request = "一只白色猫，标题原文保留"
    observed = {}

    async def provider(text, **kwargs):
        observed.update(text=text, **kwargs)
        return sample.model_copy(update={"request": text})

    monkeypatch.setattr(cli, "generate_plan", provider)
    monkeypatch.setenv("LLM_API_KEY", "test-secret")
    path = tmp_path / "new" / "plan.json"
    assert cli.main(["plan", request, "--base-url", "http://localhost:8000/v1", "--model", "local-model", "--machines", "7", "--out", str(path)]) == 0
    assert observed["text"] == request and observed["machines"] == 7
    assert observed["api_key"] == "test-secret"
    assert CreativePlan.model_validate_json(path.read_text()).request == request
    assert path.with_suffix(".md").exists()
    assert "test-secret" not in path.read_text()


def test_cli_demo_materials_can_be_recomposed_offline(tmp_path, monkeypatch):
    async def unexpected_remote_call(*args, **kwargs):
        pytest.fail("offline composition must not call an LLM or a ComfyUI worker")

    monkeypatch.setattr(cli, "generate_plan", unexpected_remote_call)
    monkeypatch.setattr(cli.Runner, "run", unexpected_remote_call)
    demo = tmp_path / "demo"
    assert cli.main(["demo", "--compose", "--out-dir", str(demo)]) == 0
    original = CreativePlan.model_validate_json((demo / "demo-plan.json").read_text())
    adjusted = original.model_copy(update={"placement": original.placement.model_copy(update={"x": 470})})
    plan = tmp_path / "adjusted-plan.json"
    plan.write_text(adjusted.model_dump_json())
    final = tmp_path / "recomposed.png"
    assert cli.main(["compose", str(plan), "--run-dir", str(demo / "run"), "--out", str(final)]) == 0
    with Image.open(final) as updated, Image.open(demo / "final.png") as initial:
        assert updated.size == (original.canvas.width, original.canvas.height)
        assert ImageChops.difference(updated, initial).getbbox() is not None


def test_missing_model_configuration_fails_without_execution(monkeypatch, capsys):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert cli.main(["plan", "一张猫的图片"]) == 1
    assert "LLM_BASE_URL" in capsys.readouterr().err


def test_schema_includes_multilayer_contract(capsys):
    assert cli.main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "extra_layers" in schema["properties"]


def test_example_profiles_load_and_all_semantic_bindings_resolve():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = ClusterConfig.model_validate_json((root / "cluster.example.json").read_text())
    assert len(config.workers) >= 3
    values = {"prompt": "subject", "negative": "blur", "width": 768, "height": 1152,
              "frames": 81, "fps": 16, "seed": 1, "image": "uploaded.png"}
    for profile in config.profiles.values():
        graph = load_workflow(profile, root)
        bound = bind_workflow(graph, profile, values)
        for name, (node, key) in profile.bindings.items():
            assert bound[node]["inputs"][key] == {**profile.defaults, **values}[name]


def test_invalid_plan_error_does_not_dump_input(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"private_input": "do-not-echo-this"}')
    assert cli.main(["report", str(path)]) == 2
    assert "do-not-echo-this" not in capsys.readouterr().err
