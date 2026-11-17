import subprocess

import pytest
from PIL import Image

from comfyui_director.compositor import add_typography, compose, overlay_video, typography_layer
from comfyui_director.models import CreativePlan


def plan(**changes):
    prompt = {"positive_zh": "测试", "positive_en": "test", "acceptance": ["test"]}
    data = {
        "title": "test",
        "request": "test",
        "canvas": {"width": 64, "height": 64},
        "visual": {
            "style": "test",
            "palette": "test",
            "lighting": "test",
            "camera": "test",
            "composition": "test",
        },
        "full_image_prompt": prompt,
        "background": prompt,
        "foreground": prompt,
        "placement": {
            "x": 16,
            "y": 16,
            "width": 32,
            "height": 32,
            "instructions": "center",
            "acceptance": ["test"],
        },
    }
    data.update(changes)
    return CreativePlan.model_validate(data)


def make_test_font(tmp_path, *, chinese=True):
    """A tiny valid font keeps typography tests independent of the host font inventory."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    characters = " ABabcdefghijklmnopqrstuvwxyz" + ("夜行计划" if chinese else "")
    cmap = {ord(char): f"g{ord(char)}" for char in characters}
    order = [".notdef", *cmap.values()]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name != cmap[32]:
            pen.moveTo((100, 0))
            pen.lineTo((500, 0))
            pen.lineTo((500, 700))
            pen.lineTo((100, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(dict.fromkeys(order, (600, 0)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "TestFont", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    output = tmp_path / "test.ttf"
    builder.save(output)
    return output


def test_alpha_cutout_preserves_background_outside_subject(tmp_path):
    background = tmp_path / "bg.png"
    foreground = tmp_path / "fg.png"
    Image.new("RGB", (64, 64), "blue").save(background)
    subject = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    # A triangular shape tests transparency, not only a rectangular paste.
    for y in range(16):
        for x in range(y + 1):
            subject.putpixel((x, y), (255, 0, 0, 255))
    subject.save(foreground)
    output = compose(plan(), background, foreground, tmp_path / "final.png")
    with Image.open(output) as image:
        assert image.size == (64, 64)
        assert image.getpixel((0, 0)) == (0, 0, 255)
        assert image.getpixel((18, 44)) == (255, 0, 0)
        assert image.getpixel((44, 18)) == (0, 0, 255)


def test_explicit_mask_multiplies_existing_alpha(tmp_path):
    background, foreground, mask = [tmp_path / name for name in ("bg.png", "fg.png", "mask.png")]
    Image.new("RGB", (64, 64), "blue").save(background)
    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(foreground)
    Image.new("L", (8, 8), 128).save(mask)
    output = compose(plan(), background, foreground, tmp_path / "result.png", mask=mask)
    with Image.open(output) as image:
        red, green, blue = image.getpixel((32, 32))
    assert 63 <= red <= 65
    assert green == 0
    assert 190 <= blue <= 192


def test_opaque_foreground_requires_explicit_cutout(tmp_path):
    background, foreground = tmp_path / "bg.png", tmp_path / "fg.png"
    Image.new("RGB", (64, 64), "blue").save(background)
    Image.new("RGB", (8, 8), "red").save(foreground)
    output = tmp_path / "final.png"
    with pytest.raises(ValueError, match="no transparency"):
        compose(plan(), background, foreground, output)
    assert not output.exists()


@pytest.mark.parametrize("mask_size,color", [((4, 4), 255), ((8, 8), 0)])
def test_invalid_or_empty_mask_fails_before_output(tmp_path, mask_size, color):
    background, foreground, mask = [tmp_path / name for name in ("bg.png", "fg.png", "mask.png")]
    Image.new("RGB", (64, 64), "blue").save(background)
    Image.new("RGB", (8, 8), "red").save(foreground)
    Image.new("L", mask_size, color).save(mask)
    output = tmp_path / "final.png"
    with pytest.raises(ValueError):
        compose(plan(), background, foreground, output, mask=mask)
    assert not output.exists()


def test_subject_keeps_aspect_ratio_and_aligns_bottom(tmp_path):
    background, foreground = tmp_path / "bg.png", tmp_path / "fg.png"
    Image.new("RGB", (64, 64), "blue").save(background)
    Image.new("RGBA", (8, 16), (255, 0, 0, 254)).save(foreground)
    output = compose(plan(), background, foreground, tmp_path / "final.png")
    with Image.open(output) as image:
        assert image.getpixel((18, 20)) == (0, 0, 255)
        assert image.getpixel((32, 20))[0] > 250
        assert image.getpixel((32, 47))[0] > 250
        assert image.getpixel((32, 48)) == (0, 0, 255)


def test_typography_requires_readable_font_before_rendering():
    text_plan = plan(text_layers=[{"text": "exact title", "x": 0, "y": 0}])
    with pytest.raises(ValueError, match="font_file"):
        typography_layer(text_plan, None)


def test_typography_overflow_is_rejected(tmp_path):
    font = make_test_font(tmp_path)
    text_plan = plan(text_layers=[{"text": "long long title", "x": 60, "y": 0}])
    with pytest.raises(ValueError, match="exceeds canvas"):
        typography_layer(text_plan, font)


def test_multiline_typography_preserves_line_breaks(tmp_path):
    font = make_test_font(tmp_path)
    text_plan = plan(text_layers=[{"text": "夜行\n计划", "x": 0, "y": 0, "font_size": 12}])
    overlay = typography_layer(text_plan, font)
    assert overlay.getbbox() is not None
    # Ink from the second line must occupy a lower row than a one-line title.
    single_plan = plan(text_layers=[{"text": "夜行", "x": 0, "y": 0, "font_size": 12}])
    single_overlay = typography_layer(single_plan, font)
    assert overlay.getbbox()[3] > single_overlay.getbbox()[3]


def test_typography_rejects_font_without_requested_chinese_glyphs(tmp_path):
    font = make_test_font(tmp_path, chinese=False)
    text_plan = plan(text_layers=[{"text": "夜行计划", "x": 0, "y": 0, "font_size": 12}])
    with pytest.raises(ValueError):
        typography_layer(text_plan, font)


def test_typography_is_added_only_to_matching_canvas(tmp_path):
    scene = tmp_path / "scene.png"
    Image.new("RGB", (64, 64), "blue").save(scene)
    overlay = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    overlay.putpixel((1, 1), (255, 0, 255, 255))
    result = add_typography(scene, overlay, tmp_path / "result.png")
    with Image.open(result) as image:
        assert image.getpixel((1, 1)) == (255, 0, 255)
        assert image.getpixel((2, 2)) == (0, 0, 255)
    with pytest.raises(ValueError, match="share a canvas"):
        add_typography(scene, Image.new("RGBA", (32, 32)), tmp_path / "bad.png")


def test_video_failure_cleans_partial_output(tmp_path, monkeypatch):
    destination = tmp_path / "final.mp4"
    monkeypatch.setattr("comfyui_director.compositor.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(
                command, 0, b'{"streams":[{"width":64,"height":64}]}'
            )
        destination.write_bytes(b"incomplete movie")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("comfyui_director.compositor.subprocess.run", fake_run)
    with pytest.raises((ValueError, subprocess.TimeoutExpired)):
        overlay_video(tmp_path / "remote.mp4", Image.new("RGBA", (64, 64)), destination)
    assert not destination.exists()
    assert not destination.with_suffix(".overlay.png").exists()


@pytest.mark.parametrize("failure", ["timeout", "oserror"])
def test_ffprobe_failure_is_safe_and_creates_no_output(tmp_path, monkeypatch, failure):
    destination = tmp_path / "final.mp4"
    monkeypatch.setattr("comfyui_director.compositor.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"private detail")
        raise OSError("private detail")

    monkeypatch.setattr("comfyui_director.compositor.subprocess.run", fake_run)
    with pytest.raises(ValueError) as caught:
        overlay_video(tmp_path / "remote.mp4", Image.new("RGBA", (64, 64)), destination)
    assert "private detail" not in str(caught.value)
    assert not destination.exists()
    assert not destination.with_suffix(".overlay.png").exists()


def test_ffmpeg_oserror_cleans_partial_output_and_hides_details(tmp_path, monkeypatch):
    destination = tmp_path / "final.mp4"
    monkeypatch.setattr("comfyui_director.compositor.shutil.which", lambda name: name)

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(
                command, 0, b'{"streams":[{"width":64,"height":64}]}'
            )
        destination.write_bytes(b"partial movie")
        raise OSError("private detail")

    monkeypatch.setattr("comfyui_director.compositor.subprocess.run", fake_run)
    with pytest.raises(ValueError) as caught:
        overlay_video(tmp_path / "remote.mp4", Image.new("RGBA", (64, 64)), destination)
    assert "private detail" not in str(caught.value)
    assert not destination.exists()
    assert not destination.with_suffix(".overlay.png").exists()
