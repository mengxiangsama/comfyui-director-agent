"""Recompose downloaded image layers without contacting a ComfyUI worker.

Only layout, layer order and typography are rendered again. Changing generation
prompts does not regenerate cached images. The source run remains read-only.
"""

from __future__ import annotations

import json
import tempfile
import warnings
from pathlib import Path

from PIL import Image

from .compositor import add_typography, compose, typography_layer
from .models import CreativePlan, PromptSpec


def _cached_file(run_dir: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("Cached artifact paths must be relative to the source run directory")
    candidate = (run_dir / value).resolve()
    if not candidate.is_relative_to(run_dir):
        raise ValueError("Cached artifact path escapes the source run directory")
    if not candidate.is_file():
        raise ValueError(f"Cached file is missing: {value}")
    return candidate


def _prompt_content(prompt: PromptSpec) -> tuple[str, str, str]:
    return prompt.positive_zh, prompt.positive_en, prompt.negative


def recompose(
    plan: CreativePlan,
    run_dir: Path,
    destination: Path,
    *,
    font_file: Path | None = None,
    cutout: str = "alpha_or_mask",
) -> Path:
    """Atomically render an image from completed cached tasks, keeping the run intact.

    ``run_dir`` must contain its original ``plan.json`` and ``run.json``. Layer
    IDs must match that snapshot, but placements, order and text may change.
    The destination must be outside the entire source run, including symlink
    aliases. PNG, JPEG and lossless WebP exports are supported. ``rembg`` is
    opt-in and may download its model; alpha/mask mode is entirely offline.
    """
    source = Path(run_dir).resolve()
    target = Path(destination).resolve()
    if not source.is_dir():
        raise ValueError("Source run directory does not exist")
    if target.is_relative_to(source):
        raise ValueError("Output must be outside the source run directory; cached files are read-only")
    formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if target.suffix.lower() not in formats:
        raise ValueError("Offline composition output must use .png, .jpg, .jpeg or .webp")
    if cutout not in {"alpha_or_mask", "rembg"}:
        raise ValueError("cutout must be alpha_or_mask or rembg")

    original = CreativePlan.model_validate_json(
        _cached_file(source, "plan.json").read_text(encoding="utf-8")
    )
    old_layers = {layer.id: layer for layer in original.extra_layers}
    if set(old_layers) != {layer.id for layer in plan.extra_layers}:
        raise ValueError("Plan layer IDs must match the cached plan snapshot; regenerate new layers first")
    journal = json.loads(_cached_file(source, "run.json").read_text(encoding="utf-8"))
    tasks = journal.get("tasks") if isinstance(journal, dict) else None
    if not isinstance(tasks, dict):
        raise ValueError("Run journal must contain cached task records")

    required = ["background", "foreground", *(layer.id for layer in plan.extra_layers)]
    cached: dict[str, dict[str, Path]] = {}
    for task_id in required:
        task = tasks.get(task_id)
        if not isinstance(task, dict) or task.get("state") != "completed":
            raise ValueError(f"Cached task is not completed: {task_id}")
        outputs = task.get("outputs")
        if not isinstance(outputs, dict) or "image" not in outputs:
            raise ValueError(f"Cached task has no image: {task_id}")
        cached[task_id] = {"image": _cached_file(source, outputs["image"])}
        if "mask" in outputs:
            cached[task_id]["mask"] = _cached_file(source, outputs["mask"])

    prompts_changed = (
        _prompt_content(plan.background) != _prompt_content(original.background)
        or _prompt_content(plan.foreground) != _prompt_content(original.foreground)
        or any(
            _prompt_content(layer.prompt) != _prompt_content(old_layers[layer.id].prompt)
            for layer in plan.extra_layers
        )
    )
    if prompts_changed:
        warnings.warn(
            "Generation prompts changed, but offline composition reuses cached images; no images are regenerated.",
            UserWarning,
            stacklevel=2,
        )
    overlay = typography_layer(plan, Path(font_file) if font_file is not None else None)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Keep all intermediate files outside the source run. A failed compose/export
    # must not damage a previous destination or any original cached output.
    with tempfile.TemporaryDirectory(prefix=".comfy-director-compose-", dir=target.parent) as temporary:
        scratch = Path(temporary)
        scene = scratch / "scene.png"
        subject = cached["foreground"]
        compose(
            plan, cached["background"]["image"], subject["image"], scene,
            mask=subject.get("mask"), cutout=cutout,
        )
        for layer in plan.extra_layers:
            subject = cached[layer.id]
            compose(
                plan.model_copy(update={"placement": layer.placement}),
                scene, subject["image"], scene,
                mask=subject.get("mask"), cutout=cutout,
            )
        rendered = scratch / "rendered.png"
        add_typography(scene, overlay, rendered)
        export = scratch / f"export{target.suffix.lower()}"
        image_format = formats[target.suffix.lower()]
        with Image.open(rendered) as image:
            options = {"quality": 95} if image_format == "JPEG" else {}
            if image_format == "WEBP":
                options["lossless"] = True
            image.save(export, format=image_format, **options)
        export.replace(target)
    return target
