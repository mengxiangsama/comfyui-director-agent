"""Deterministic image composition and typography after GPU generation."""

import json
import shutil
import subprocess
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError
from PIL import Image, ImageChops, ImageColor, ImageFilter, ImageFont, ImageOps

from .models import CreativePlan


def typography_layer(plan: CreativePlan, font_file: Path | None) -> Image.Image:
    from PIL import ImageDraw

    layer = Image.new("RGBA", (plan.canvas.width, plan.canvas.height), (0, 0, 0, 0))
    if not plan.text_layers:
        return layer
    if font_file is None or not font_file.is_file():
        raise ValueError("Text requires font_file pointing to a font with all requested glyphs")
    try:
        with TTFont(str(font_file), fontNumber=0, lazy=True) as font_data:
            cmap = font_data.getBestCmap() or {}
            required = {ord(ch) for item in plan.text_layers for ch in item.text if not ch.isspace()}
            if any(code not in cmap or cmap[code] == ".notdef" for code in required):
                raise ValueError("Configured font does not cover all requested text glyphs")
    except (TTLibError, OSError, IndexError):
        raise ValueError("Cannot read configured TrueType/OpenType font") from None
    draw = ImageDraw.Draw(layer)
    for item in plan.text_layers:
        font = ImageFont.truetype(str(font_file), item.font_size)
        color = ImageColor.getcolor(item.color, "RGBA")
        # Pillow does not support top/bottom anchors for multiline text. Measure
        # using a baseline anchor, then translate visible ink to the requested top-left.
        box = draw.multiline_textbbox((0, 0), item.text, font=font, anchor="la")
        if item.x + box[2] - box[0] > layer.width or item.y + box[3] - box[1] > layer.height:
            raise ValueError("Text exceeds canvas; reduce font_size or change x/y")
        draw.multiline_text((item.x - box[0], item.y - box[1]), item.text, font=font, fill=color, anchor="la")
    return layer


def compose(
    plan: CreativePlan,
    background: Path,
    foreground: Path,
    destination: Path,
    *,
    mask: Path | None = None,
    cutout: str = "alpha_or_mask",
) -> Path:
    with Image.open(background) as source:
        base = ImageOps.fit(
            ImageOps.exif_transpose(source).convert("RGBA"),
            (plan.canvas.width, plan.canvas.height),
            method=Image.Resampling.LANCZOS,
        )
    with Image.open(foreground) as source:
        subject = ImageOps.exif_transpose(source).convert("RGBA")
    if mask is not None:
        with Image.open(mask) as source:
            alpha = ImageOps.exif_transpose(source).convert("L")
        if alpha.size != subject.size:
            raise ValueError("Foreground mask must have the same size as foreground image")
        subject.putalpha(ImageChops.multiply(subject.getchannel("A"), alpha))
    elif subject.getchannel("A").getextrema() == (255, 255):
        if cutout != "rembg":
            raise ValueError("Foreground has no transparency: supply a white-subject mask or enable rembg")
        try:
            from rembg import remove
        except ImportError as exc:
            raise ValueError("Automatic cutout requires pip install 'comfyui-director-agent[cutout]'") from exc
        subject = remove(subject).convert("RGBA")
    alpha = subject.getchannel("A")
    bounds = alpha.getbbox()
    if bounds is None:
        raise ValueError("Foreground mask is empty")
    subject = subject.crop(bounds)
    box = plan.placement
    subject = ImageOps.contain(subject, (box.width, box.height), Image.Resampling.LANCZOS)
    if box.feather_px:
        subject.putalpha(subject.getchannel("A").filter(ImageFilter.GaussianBlur(box.feather_px)))
    # Keep aspect ratio; center horizontally and align the bottom to the placement box.
    position = (box.x + (box.width - subject.width) // 2, box.y + box.height - subject.height)
    base.alpha_composite(subject, position)
    destination.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(destination, format="PNG")
    return destination


def add_typography(image_path: Path, overlay: Image.Image, destination: Path) -> Path:
    with Image.open(image_path) as source:
        image = source.convert("RGBA")
    if overlay.size != image.size:
        raise ValueError("Typography and scene must share a canvas")
    image.alpha_composite(overlay)
    image.convert("RGB").save(destination, format="PNG")
    return destination


def overlay_video(video_path: Path, overlay: Image.Image, destination: Path) -> Path:
    """Overlay exact text after diffusion, never ask a video model to preserve lettering."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("Video typography requires ffmpeg and ffprobe on PATH")
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "json", str(video_path)],
            capture_output=True, timeout=30, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise ValueError("Could not inspect generated video") from None
    try:
        stream = json.loads(probe.stdout)["streams"][0]
        size = (int(stream["width"]), int(stream["height"]))
        if probe.returncode or min(size) < 1 or max(size) > 8192:
            raise ValueError
    except (ValueError, KeyError, IndexError) as exc:
        raise ValueError("Cannot determine generated video dimensions") from exc
    overlay_path = destination.with_suffix(".overlay.png")
    overlay.resize(size, Image.Resampling.LANCZOS).save(overlay_path)
    try:
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-y", "-i", str(video_path), "-i", str(overlay_path),
             "-filter_complex", "[0:v][1:v]overlay=0:0:eof_action=repeat[v]", "-map", "[v]",
             "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
             "-c:a", "copy", "-movflags", "+faststart", str(destination)],
            capture_output=True, timeout=600, check=False,
        )
        if result.returncode:
            destination.unlink(missing_ok=True)
            raise ValueError("ffmpeg could not overlay video typography")
    except (subprocess.TimeoutExpired, OSError):
        destination.unlink(missing_ok=True)
        raise ValueError("Video typography encoding failed or timed out") from None
    finally:
        overlay_path.unlink(missing_ok=True)
    return destination
