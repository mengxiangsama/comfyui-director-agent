import hashlib
import json

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from PIL import Image

from comfyui_director.demo import build_demo
from comfyui_director.models import CreativePlan
from comfyui_director.offline import recompose


def make_plan(*, extras=()):
    prompt = {"positive_zh": "测试素材", "positive_en": "test asset", "acceptance": ["test"]}
    placement = {
        "x": 16, "y": 16, "width": 32, "height": 32,
        "instructions": "place subject", "acceptance": ["test"],
    }
    return CreativePlan.model_validate({
        "title": "offline test", "request": "cached layers",
        "canvas": {"width": 64, "height": 64},
        "visual": {name: "test" for name in ("style", "palette", "lighting", "camera", "composition")},
        "full_image_prompt": prompt, "background": prompt, "foreground": prompt,
        "placement": placement,
        "extra_layers": [{"id": name, "prompt": prompt, "placement": placement} for name in extras],
    })


def make_run(tmp_path, plan=None):
    plan = plan or make_plan()
    run = tmp_path / "run"
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    Image.new("RGB", (64, 64), "blue").save(artifacts / "background.png")
    colors = ["red", "lime", "yellow"]
    ids = ["foreground", *(layer.id for layer in plan.extra_layers)]
    for name, color in zip(ids, colors, strict=False):
        image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        image.paste(color, (2, 2, 14, 14))
        image.save(artifacts / f"{name}.png")
    (run / "plan.json").write_text(plan.model_dump_json())
    tasks = {
        name: {"state": "completed", "outputs": {"image": f"artifacts/{name}.png"}}
        for name in ("background", *ids)
    }
    # A failed/unfinished later stage must not prevent composing completed material.
    (run / "run.json").write_text(json.dumps({"state": "needs_attention", "tasks": tasks}))
    (run / "final.png").write_bytes(b"original final must remain unchanged")
    return plan, run


def snapshot(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def test_offline_reposition_uses_cached_pixels_and_preserves_entire_run(tmp_path):
    plan, run = make_run(tmp_path)
    before = snapshot(run)
    modified = plan.model_copy(update={"placement": plan.placement.model_copy(update={"x": 0, "y": 0})})
    result = recompose(modified, run, tmp_path / "repositioned.png")
    with Image.open(result) as image:
        assert image.getpixel((8, 8)) == (255, 0, 0)
        assert image.getpixel((40, 40)) == (0, 0, 255)
    assert snapshot(run) == before


def test_reordering_layers_changes_overlap_without_regeneration(tmp_path):
    plan, run = make_run(tmp_path, make_plan(extras=("green", "yellow")))
    original_order = recompose(plan, run, tmp_path / "original-order.png")
    reversed_plan = plan.model_copy(update={"extra_layers": list(reversed(plan.extra_layers))})
    reversed_order = recompose(reversed_plan, run, tmp_path / "reversed-order.png")
    with Image.open(original_order) as first, Image.open(reversed_order) as second:
        assert first.getpixel((32, 32)) == (255, 255, 0)
        assert second.getpixel((32, 32)) == (0, 255, 0)


def make_font(path):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({ord("A"): "A"})
    glyphs = {}
    for name in (".notdef", "A"):
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((500, 0))
        pen.lineTo((500, 700))
        pen.lineTo((0, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "OfflineTest", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.save(path)
    return path


def test_new_text_is_rendered_with_explicit_font(tmp_path):
    plan, run = make_run(tmp_path)
    data = plan.model_dump()
    data["text_layers"] = [{"text": "A", "x": 0, "y": 0, "font_size": 12, "color": "#FFFFFF"}]
    text_plan = CreativePlan.model_validate(data)
    font = make_font(tmp_path / "font.ttf")
    result = recompose(text_plan, run, tmp_path / "with-text.png", font_file=font)
    with Image.open(result) as image:
        assert image.getpixel((2, 2)) == (255, 255, 255)
        assert image.getpixel((32, 32)) == (255, 0, 0)


@pytest.mark.parametrize("relative", ["final.png", "new.png", "artifacts/foreground.png", "nested/new.png"])
def test_destination_cannot_write_anywhere_in_source_run(tmp_path, relative):
    plan, run = make_run(tmp_path)
    before = snapshot(run)
    with pytest.raises(ValueError, match="outside the source run"):
        recompose(plan, run, run / relative)
    assert snapshot(run) == before


def test_output_symlink_alias_into_run_is_rejected(tmp_path):
    plan, run = make_run(tmp_path)
    link = tmp_path / "alias"
    link.symlink_to(run, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the source run"):
        recompose(plan, run, link / "new.png")


@pytest.mark.parametrize("output_name", ["image", "mask"])
@pytest.mark.parametrize("escape", ["parent", "absolute", "symlink"])
def test_cached_image_and_mask_paths_cannot_escape_run(tmp_path, output_name, escape):
    plan, run = make_run(tmp_path)
    outside = tmp_path / "private.png"
    Image.new("RGB", (16, 16), "purple").save(outside)
    if escape == "parent":
        value = "../private.png"
    elif escape == "absolute":
        value = str(outside)
    else:
        (run / "alias.png").symlink_to(outside)
        value = "alias.png"
    journal = json.loads((run / "run.json").read_text())
    journal["tasks"]["foreground"]["outputs"][output_name] = value
    (run / "run.json").write_text(json.dumps(journal))
    with pytest.raises(ValueError, match="relative|escapes"):
        recompose(plan, run, tmp_path / "result.png")
    assert not (tmp_path / "result.png").exists()


def test_required_mask_is_applied_to_cached_opaque_foreground(tmp_path):
    plan, run = make_run(tmp_path)
    Image.new("RGB", (16, 16), "red").save(run / "artifacts/foreground.png")
    mask = Image.new("L", (16, 16), 0)
    mask.paste(255, (0, 0, 8, 16))
    mask.save(run / "artifacts/mask.png")
    journal = json.loads((run / "run.json").read_text())
    journal["tasks"]["foreground"]["outputs"]["mask"] = "artifacts/mask.png"
    (run / "run.json").write_text(json.dumps(journal))
    result = recompose(plan, run, tmp_path / "masked.png")
    with Image.open(result) as image:
        assert image.getpixel((32, 24)) == (255, 0, 0)
        assert image.getpixel((20, 24)) == (0, 0, 255)


@pytest.mark.parametrize("problem", ["incomplete", "missing-task", "missing-image", "missing-file"])
def test_only_completed_available_material_is_accepted(tmp_path, problem):
    plan, run = make_run(tmp_path)
    journal = json.loads((run / "run.json").read_text())
    if problem == "incomplete":
        journal["tasks"]["foreground"]["state"] = "submitted"
    elif problem == "missing-task":
        del journal["tasks"]["foreground"]
    elif problem == "missing-image":
        del journal["tasks"]["foreground"]["outputs"]["image"]
    else:
        (run / "artifacts/foreground.png").unlink()
    (run / "run.json").write_text(json.dumps(journal))
    with pytest.raises(ValueError):
        recompose(plan, run, tmp_path / "result.png")
    assert not (tmp_path / "result.png").exists()


def test_changed_layer_identity_is_rejected_even_with_matching_task_record(tmp_path):
    plan, run = make_run(tmp_path, make_plan(extras=("green",)))
    modified = plan.model_dump()
    modified["extra_layers"][0]["id"] = "renamed"
    journal = json.loads((run / "run.json").read_text())
    journal["tasks"]["renamed"] = journal["tasks"]["green"]
    (run / "run.json").write_text(json.dumps(journal))
    with pytest.raises(ValueError, match="layer IDs"):
        recompose(CreativePlan.model_validate(modified), run, tmp_path / "result.png")


def test_generation_prompt_changes_warn_and_reuse_old_asset(tmp_path):
    plan, run = make_run(tmp_path)
    changed = plan.model_copy(update={"foreground": plan.foreground.model_copy(update={"positive_en": "a green subject"})})
    with pytest.warns(UserWarning, match="no images are regenerated"):
        result = recompose(changed, run, tmp_path / "same-pixels.png")
    with Image.open(result) as image:
        assert image.getpixel((32, 32)) == (255, 0, 0)


def test_failed_render_preserves_existing_destination_and_cleans_scratch(tmp_path, monkeypatch):
    plan, run = make_run(tmp_path)
    destination = tmp_path / "result.png"
    destination.write_bytes(b"previous completed result")
    before = snapshot(run)

    def fail_typography(source, overlay, output):
        output.write_bytes(b"partial result")
        raise ValueError("simulated render failure")

    monkeypatch.setattr("comfyui_director.offline.add_typography", fail_typography)
    with pytest.raises(ValueError, match="simulated"):
        recompose(plan, run, destination)
    assert destination.read_bytes() == b"previous completed result"
    assert snapshot(run) == before
    assert not list(tmp_path.glob(".comfy-director-compose-*"))


@pytest.mark.parametrize("extension,image_format", [("png", "PNG"), ("jpg", "JPEG"), ("webp", "WEBP")])
def test_export_uses_requested_image_encoding(tmp_path, extension, image_format):
    plan, run = make_run(tmp_path)
    output = recompose(plan, run, tmp_path / f"result.{extension}")
    with Image.open(output) as image:
        assert image.format == image_format


def test_demo_builds_real_deterministic_composite_without_external_resources(tmp_path, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient.request", lambda *args, **kwargs: pytest.fail("demo must not use HTTP"))
    first = build_demo(tmp_path / "first")
    second = build_demo(tmp_path / "second")
    assert first.name == "final.png"
    assert first.read_bytes() == second.read_bytes()
    plan = CreativePlan.model_validate_json((first.parent / "demo-plan.json").read_text())
    assert not plan.text_layers
    with Image.open(first.parent / "foreground.png") as foreground:
        assert foreground.mode == "RGBA"
        assert foreground.getpixel((0, 0))[3] == 0
        assert foreground.getchannel("A").getextrema() == (0, 255)
    with Image.open(first.parent / "background.png") as background, Image.open(first) as composed:
        assert composed.size == (960, 640)
        assert composed.getpixel((20, 20)) == background.getpixel((20, 20))
        assert composed.getpixel((650, 350)) != background.getpixel((650, 350))
    journal = json.loads((first.parent / "run/run.json").read_text())
    assert journal["demo"] is True
    assert all(task["source"] == "pillow_demo" for task in journal["tasks"].values())
    before = snapshot(first.parent)
    with pytest.raises(ValueError, match="already exists"):
        build_demo(first.parent)
    assert snapshot(first.parent) == before
