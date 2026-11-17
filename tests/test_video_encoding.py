import io
import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from comfyui_director.compositor import overlay_video


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg/ffprobe not installed")
def test_real_video_overlay_preserves_duration_audio_and_background(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=64x64:r=8:d=1", "-f", "lavfi", "-i",
         "sine=frequency=1000:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True, timeout=30,
    )
    overlay = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle((16, 16, 48, 48), fill=(255, 0, 255, 255))
    result = overlay_video(source, overlay, tmp_path / "final.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(result)],
        check=True, capture_output=True, timeout=30,
    )
    metadata = json.loads(probe.stdout)
    assert {s["codec_type"] for s in metadata["streams"]} == {"audio", "video"}
    assert abs(float(metadata["format"]["duration"]) - 1) < 0.2
    frame = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(result), "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
        check=True, capture_output=True, timeout=30,
    )
    with Image.open(io.BytesIO(frame.stdout)) as image:
        assert image.size == (64, 64)
        r, g, b = image.convert("RGB").getpixel((16, 16))
        assert r > 180 and b > 180 and g < 70
        r, g, b = image.convert("RGB").getpixel((50, 50))
        assert b > 180 and r < 70 and g < 70
    assert not result.with_suffix(".overlay.png").exists()
