"""Deterministic Pillow illustration demonstrating real alpha composition, not AI output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import CreativePlan
from .offline import recompose


def _background() -> Image.Image:
    image = Image.new("RGB", (960, 640))
    draw = ImageDraw.Draw(image)
    for y in range(640):
        factor = y / 639
        draw.line((0, y, 959, y), fill=(round(239 - 18 * factor), round(247 - 10 * factor), round(244 - 8 * factor)))
    draw.rounded_rectangle((28, 28, 932, 612), radius=28, outline="#BCCFCA", width=2)
    draw.rounded_rectangle((66, 70, 249, 102), radius=16, fill="#D3E7DF")
    draw.text((83, 78), "LOCAL COMPOSE DEMO", font=ImageFont.load_default(size=15), fill="#2F6258")
    draw.multiline_text((68, 147), "One scene.\nSeparate layers.", font=ImageFont.load_default(size=42), fill="#183E39", spacing=12)
    draw.multiline_text((70, 285), "A background + a transparent subject.\nRecomposed locally, with no GPU.\n\nDeterministic Pillow illustration.", font=ImageFont.load_default(size=18), fill="#517169", spacing=10)
    draw.line((70, 450, 343, 450), fill="#B5CDC3", width=2)
    for index, text in enumerate(("01   BACKGROUND", "02   ALPHA LAYER", "03   FINAL IMAGE")):
        draw.text((70, 471 + index * 29), text, font=ImageFont.load_default(size=14), fill="#497067")

    # A fixed studio stage is part of this synthetic illustration, not an
    # automatic shadow-generation feature of the compositor.
    draw.ellipse((406, 462, 885, 581), fill="#BAD5CA")
    draw.ellipse((406, 444, 885, 557), fill="#F7FAEF", outline="#A6C4B7", width=2)
    draw.arc((437, 458, 854, 544), start=0, end=178, fill="#D3E2D5", width=2)
    for index in range(16, 0, -1):
        shade = 218 - round((16 - index) * 2.0)
        draw.ellipse((527 - index, 508 - index // 3, 779 + index, 534 + index // 3), fill=(shade - 20, shade + 5, shade - 10))
    draw.ellipse((518, 492, 537, 511), fill="#8BBDB0")
    draw.ellipse((835, 472, 854, 491), fill="#E7B367")
    draw.line((796, 166, 796, 195), fill="#76A99A", width=3)
    draw.line((782, 180, 810, 180), fill="#76A99A", width=3)
    draw.ellipse((855, 269, 864, 278), fill="#83ADA0")
    draw.text((725, 578), "PILLOW / NO GPU", font=ImageFont.load_default(size=13), fill="#497067")
    return image


def _foreground() -> Image.Image:
    scale = 2
    image = Image.new("RGBA", (400 * scale, 400 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(coords):
        return tuple(round(value * scale) for value in coords)

    def rounded(coords, radius, fill, outline=None, width=1):
        draw.rounded_rectangle(box(coords), radius=radius * scale, fill=fill, outline=outline, width=width * scale)

    # Rear leg, curved mechanical tail and torso sit behind the head.
    draw.line([(int(x * scale), int(y * scale)) for x, y in [(285, 285), (333, 265), (354, 212), (339, 187)]], fill="#8B4C2E", width=28 * scale, joint="curve")
    draw.line([(int(x * scale), int(y * scale)) for x, y in [(285, 280), (331, 259), (348, 215), (335, 190)]], fill="#EFA34E", width=17 * scale, joint="curve")
    rounded((123, 205, 281, 329), 54, "#B96135", "#754B35", 3)
    rounded((122, 197, 275, 316), 51, "#EF9C49", "#8F5D38", 3)
    rounded((143, 212, 252, 285), 35, "#F7BC6C")
    rounded((133, 298, 177, 356), 17, "#A15E3A", "#805238", 2)
    rounded((224, 298, 266, 355), 17, "#A15E3A", "#805238", 2)
    rounded((118, 330, 180, 365), 13, "#F2AD59", "#85543A", 2)
    rounded((219, 329, 281, 365), 13, "#F2AD59", "#85543A", 2)
    draw.line(box((141, 342, 141, 357)), fill="#BE7B42", width=2 * scale)
    draw.line(box((242, 341, 242, 356)), fill="#BE7B42", width=2 * scale)
    # Pointed cat ears use an orange shell and dark inset panels.
    draw.polygon([box((104, 134)), box((110, 37)), box((172, 96))], fill="#F5AD57", outline="#85543A", width=3 * scale)
    draw.polygon([box((230, 94)), box((290, 38)), box((297, 137))], fill="#E99B4B", outline="#85543A", width=3 * scale)
    draw.polygon([box((117, 113)), box((123, 66)), box((151, 97))], fill="#587F70")
    draw.polygon([box((251, 99)), box((279, 67)), box((283, 117))], fill="#587F70")
    rounded((83, 88, 312, 232), 56, "#C9763E", "#85543A", 3)
    rounded((83, 81, 307, 220), 56, "#F5AD57", "#85543A", 3)
    draw.arc(box((99, 91, 286, 207)), start=195, end=280, fill="#FFD491", width=7 * scale)
    rounded((105, 116, 287, 188), 28, "#264F48", "#42685B", 2)
    rounded((128, 138, 166, 159), 9, "#A8EDCB")
    rounded((226, 138, 264, 159), 9, "#A8EDCB")
    draw.line(box((189, 174, 203, 174)), fill="#ABD8BD", width=3 * scale)
    draw.ellipse(box((105, 192, 117, 204)), fill="#D18845")
    draw.ellipse(box((276, 192, 288, 204)), fill="#D18845")
    rounded((176, 238, 221, 273), 9, "#416A59", "#698A69", 2)
    draw.ellipse(box((188, 247, 208, 265)), fill="#A9D4A5")
    for x, y in [(94, 146), (295, 146), (140, 289), (251, 289)]:
        draw.ellipse(box((x - 3, y - 3, x + 3, y + 3)), fill="#8A6544")
    return image.resize((400, 400), Image.Resampling.LANCZOS)


def build_demo(output_dir: Path) -> Path:
    """Create local demo assets and run the same offline compositor used by the CLI.

    Produces ``background.png``, transparent ``foreground.png``, ``final.png``,
    ``demo-plan.json`` and a readable source cache under ``run/``. Nothing is
    downloaded; Pillow's bundled font handles the illustration labels.
    """
    output_dir = Path(output_dir).resolve()
    run_dir = output_dir / "run"
    artifacts = run_dir / "artifacts"
    if run_dir.exists() or any((output_dir / name).exists() for name in ("background.png", "foreground.png", "final.png", "demo-plan.json")):
        raise ValueError("Demo output already exists; choose a new output directory")
    artifacts.mkdir(parents=True, exist_ok=True)
    _background().save(artifacts / "background.png", optimize=True)
    _foreground().save(artifacts / "foreground.png", optimize=True)
    prompt = {
        "positive_zh": "确定性Pillow演示素材，不是AI或GPU生成。",
        "positive_en": "A deterministic Pillow demo asset, not AI or GPU output.",
        "acceptance": ["示意素材轮廓完整，色彩和光照一致。"],
    }
    plan = CreativePlan.model_validate({
        "title": "Offline composition demo / Pillow / no GPU",
        "request": "用本地Pillow绘制背景和透明机器人猫，再通过真实离线合成流程输出成片。",
        "canvas": {"width": 960, "height": 640},
        "visual": {
            "style": "清爽矢量插画演示",
            "palette": "浅薄荷背景、橙色主体、深青色细节",
            "lighting": "左上方柔光，与预绘舞台阴影保持一致",
            "camera": "轻微俯视的正面视角",
            "composition": "左侧说明，右侧平台与主体",
        },
        "full_image_prompt": prompt,
        "background": prompt,
        "foreground": prompt,
        "placement": {
            "x": 494, "y": 173, "width": 330, "height": 362,
            "feather_px": 0,
            "instructions": "保持比例，把机器人猫居中放到预绘平台上，底部对齐。",
            "acceptance": ["背景可透过主体之外的透明区域，脚部与平台落点协调。"],
        },
        "assumptions": ["所有图形与文字标注由Pillow确定性绘制；未调用LLM、ComfyUI、GPU或外部字体。"],
    })
    serialized = plan.model_dump_json(indent=2)
    (run_dir / "plan.json").write_text(serialized, encoding="utf-8")
    (output_dir / "demo-plan.json").write_text(serialized, encoding="utf-8")
    journal = {
        "run_id": "pillow-offline-demo",
        "state": "materials_ready",
        "demo": True,
        "note": "Deterministic Pillow illustration. No AI model or GPU was used.",
        "tasks": {
            name: {"state": "completed", "source": "pillow_demo", "outputs": {"image": f"artifacts/{name}.png"}}
            for name in ("background", "foreground")
        },
    }
    (run_dir / "run.json").write_text(json.dumps(journal, indent=2), encoding="utf-8")
    for name in ("background", "foreground"):
        shutil.copyfile(artifacts / f"{name}.png", output_dir / f"{name}.png")
    return recompose(plan, run_dir, output_dir / "final.png")
