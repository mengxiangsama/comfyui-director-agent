from comfyui_director.models import CreativePlan
from comfyui_director.report import render_markdown


def make_plan(*, video=False):
    def prompt(name):
        return {
            "positive_zh": f"{name}中文提示词",
            "positive_en": f"{name} English prompt",
            "negative": f"{name}负向提示词",
            "acceptance": [f"{name}验收要求"],
        }

    placement = {
        "x": 100, "y": 200, "width": 400, "height": 500, "feather_px": 2,
        "instructions": "保持比例，使用前景遮罩，阴影方向与背景一致。",
        "acceptance": ["边缘没有白边"],
    }
    return CreativePlan.model_validate({
        "title": "测试创作计划",
        "request": "原始中文需求",
        "medium": "video" if video else "image",
        "canvas": {"width": 1024, "height": 1024},
        "visual": {
            "style": "写实", "palette": "蓝色", "lighting": "左上柔光",
            "camera": "平视", "composition": "左侧留白", "constraints": ["不要改变颜色"],
        },
        "full_image_prompt": prompt("完整图"),
        "background": prompt("背景"),
        "foreground": prompt("主前景"),
        "placement": placement,
        "extra_layers": [{
            "id": "second_object", "prompt": prompt("第二物体"),
            "placement": {**placement, "x": 600, "width": 300},
        }],
        "text_layers": [{"text": "新品|¥99\n立即购买", "x": 10, "y": 30, "font_size": 48}],
        "video": {
            "positive_zh": "镜头缓慢推进，主体不动。",
            "positive_en": "Slowly push the camera in while the subject remains still.",
            "negative": "闪烁", "duration_seconds": 5, "fps": 16, "frames": 81,
            "acceptance": ["运动连贯"],
        } if video else None,
        "assumptions": ["用户未指定画幅，采用方形"],
    })


def test_report_contains_prompts_assignment_layers_and_layout():
    plan = make_plan()
    report = render_markdown(plan)
    assert "尚未执行或验收" in report
    assert "原始中文需求" in report
    assert "background/foreground/video" in report
    assert "不固定两台" in report
    for value in [plan.full_image_prompt, plan.background, plan.foreground, plan.extra_layers[0].prompt]:
        assert value.positive_zh in report
        assert value.positive_en in report
        assert value.negative in report
        assert value.acceptance[0] in report
    assert "second_object" in report
    assert "越靠后越位于画面上层" in report
    assert "x=100，y=200" in report
    assert "width=400，height=500" in report
    assert "x=600，y=200" in report
    assert "白色/1 表示保留前景" in report
    assert "不会自动重新布光" in report
    assert "新品\\|¥99 立即购买" in report
    assert "新品|¥99\n立即购买" in report
    assert "覆盖所需中文字符" in report
    assert "采用方形" in report
    assert "## 视频 worker：图生视频" not in report


def test_video_report_exposes_timing_and_post_generation_typography():
    report = render_markdown(make_plan(video=True))
    assert "## 视频 worker：图生视频" in report
    assert "目标时长：5 秒" in report
    assert "生成帧数：81" in report
    assert "输出帧率：16 fps" in report
    assert "5.062" in report
    assert "镜头缓慢推进" in report
    assert "ffmpeg 后期叠加" in report
    assert "无文案的合成图片作为首帧" in report
    assert "在视频生成完成后叠加" in report


def test_prompt_fences_cannot_break_report_structure():
    plan = make_plan()
    plan.foreground.positive_zh = "保留```text标记和````四个反引号"
    report = render_markdown(plan)
    assert "`````text\n保留```text标记和````四个反引号\n`````" in report
