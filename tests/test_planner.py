import json

import httpx
import pytest

from comfyui_director.planner import PlannerError, generate_plan


def plan_data(*, video=False):
    prompt = {
        "positive_zh": "银色水杯位于右侧，左上方柔光，冷灰色商业摄影。",
        "positive_en": "A silver cup on the right, soft light from upper left, cool grey commercial photography.",
        "negative": "模糊，水印",
        "acceptance": ["保留银色水杯与左侧文字留白"],
    }
    data = {
        "title": "水杯广告",
        "request": "模型错误改写的原始需求",
        "medium": "video" if video else "image",
        "canvas": {"width": 1024, "height": 1024},
        "visual": {
            "style": "商业摄影",
            "palette": "冷灰与银色",
            "lighting": "左上方大面积柔光，阴影落向右下",
            "camera": "平视，透视统一",
            "composition": "水杯右侧，左侧文案留白",
            "constraints": ["水杯不可改色"],
        },
        "full_image_prompt": prompt,
        "background": {**prompt, "positive_zh": "冷灰摄影棚背景，右侧保留主体位置，无水杯，无文字。"},
        "foreground": prompt,
        "placement": {
            "x": 512,
            "y": 256,
            "width": 400,
            "height": 600,
            "instructions": "保持比例缩放后，用前景 alpha 合成到右侧。",
            "acceptance": ["前景不越界"],
        },
        "extra_layers": [],
        "text_layers": [{"text": "今天¥99", "x": 48, "y": 100, "font_size": 60}],
        "video": None,
        "assumptions": ["默认方形画布"],
    }
    if video:
        data["video"] = {
            "positive_zh": "水杯保持原位，镜头缓慢推进，高光沿杯壁移动。",
            "positive_en": "Keep the cup in place while the camera slowly pushes in and a highlight moves along its surface.",
            "negative": "闪烁，形态改变",
            "duration_seconds": 5,
            "fps": 16,
            "frames": 81,
            "acceptance": ["杯身形态不变，运动连贯"],
        }
    return data


def reply(data):
    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@pytest.mark.asyncio
async def test_real_provider_contract_and_preserved_request():
    requests = []
    original = "做一张银色水杯海报，写‘今天¥99’"

    def provider(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url == "https://provider.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-key"
        assert body["model"] == "configured-model"
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][1] == {"role": "user", "content": original}
        system = body["messages"][0]["content"]
        assert "全品类提示词扩写" in system  # Actual vendored template is used.
        assert "extra_layers" in system
        assert "用户提供的可用机器数：6" in system
        assert '"$defs"' in system  # Full schema, including nested model definitions.
        assert "text_layers" in system and "逐字" in system
        return reply(plan_data())

    result = await generate_plan(
        original,
        base_url="https://provider.example/v1/",
        model="configured-model",
        api_key="unit-test-key",
        machines=6,
        transport=httpx.MockTransport(provider),
    )
    assert result.request == original
    assert result.text_layers[0].text == "今天¥99"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_invalid_json_repairs_once():
    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        return reply("not json") if len(calls) == 1 else reply(plan_data())

    result = await generate_plan(
        "银色水杯", base_url="http://localhost:1234/v1", model="local",
        transport=httpx.MockTransport(provider),
    )
    assert result.title == "水杯广告"
    assert len(calls) == 2
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": "not json"}
    assert "修复" in calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_invalid_layout_is_repaired_instead_of_accepted():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        data = plan_data()
        if calls == 1:
            data["placement"]["x"] = 1000
        return reply(data)

    result = await generate_plan(
        "水杯", base_url="https://provider.example/v1", model="m",
        transport=httpx.MockTransport(provider),
    )
    assert calls == 2
    assert result.placement.x == 512


@pytest.mark.asyncio
async def test_repeated_invalid_json_stops_without_exposing_content():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        return reply("secret-from-provider not-json")

    with pytest.raises(PlannerError, match="连续两次") as error:
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m", api_key="secret-key",
            transport=httpx.MockTransport(provider),
        )
    assert calls == 2
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_video_and_multiple_foregrounds_are_validated():
    data = plan_data(video=True)
    data["extra_layers"] = [{
        "id": "lemon",
        "prompt": data["foreground"],
        "placement": {**data["placement"], "x": 10, "y": 800, "width": 100, "height": 100},
    }]
    result = await generate_plan(
        "做一段水杯与柠檬的视频", base_url="https://provider.example/v1", model="m",
        transport=httpx.MockTransport(lambda request: reply(data)),
    )
    assert result.medium == "video"
    assert result.video.frames == 81
    assert result.extra_layers[0].id == "lemon"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_http_error_does_not_leak_key_or_body(status):
    def provider(request):
        return httpx.Response(status, text="sensitive provider body, secret-key")

    with pytest.raises(PlannerError, match=str(status)) as error:
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m", api_key="secret-key",
            transport=httpx.MockTransport(provider),
        )
    assert "sensitive" not in str(error.value)
    assert "secret-key" not in str(error.value)


@pytest.mark.asyncio
async def test_transport_error_is_redacted():
    def provider(request):
        raise httpx.ConnectError("secret-key in low-level exception", request=request)

    with pytest.raises(PlannerError, match="无法连接") as error:
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m",
            transport=httpx.MockTransport(provider),
        )
    assert "secret-key" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://user:secret@provider.example/v1",
    "https://provider.example/v1?key=secret", "https://provider.example/v1#secret",
    "https://provider.example:wrong/v1",
])
async def test_invalid_url_is_rejected_without_echoing_credentials(url):
    with pytest.raises(PlannerError, match="模型地址") as error:
        await generate_plan("水杯", base_url=url, model="m")
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_redirect_is_not_followed_with_authorization():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        return httpx.Response(307, headers={"Location": "https://other.example/collect"})

    with pytest.raises(PlannerError, match="重定向"):
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m", api_key="secret-key",
            transport=httpx.MockTransport(provider),
        )
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("machines", [0, -1, True, "2"])
async def test_invalid_machine_count(machines):
    with pytest.raises(PlannerError, match="正整数"):
        await generate_plan("水杯", base_url="https://provider.example/v1", model="m", machines=machines)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["not json", "{}", "[]", '{"choices": []}'])
async def test_invalid_provider_envelope_is_safe(body):
    with pytest.raises(PlannerError, match="未返回有效"):
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)),
        )


@pytest.mark.asyncio
async def test_timeout_does_not_expose_transport_message():
    def provider(request):
        raise httpx.ReadTimeout("secret-key in timeout", request=request)

    with pytest.raises(PlannerError, match="超时") as error:
        await generate_plan(
            "水杯", base_url="https://provider.example/v1", model="m",
            transport=httpx.MockTransport(provider),
        )
    assert "secret-key" not in str(error.value)


def with_text(*texts):
    data = plan_data()
    data["text_layers"] = [
        {"text": text, "x": 10, "y": 10 + index * 40, "font_size": 24, "color": "#111111"}
        for index, text in enumerate(texts)
    ]
    return data


@pytest.mark.asyncio
async def test_explicit_exact_text_is_passed_as_data_and_kept_character_for_character():
    exact = ('Spring "Care"\n30 mL / ¥199',)
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return reply(with_text(*exact))

    result = await generate_plan(
        "做一张精华海报", base_url="https://provider.example/v1", model="m", exact_text=exact,
        transport=httpx.MockTransport(provider),
    )
    assert [layer.text for layer in result.text_layers] == list(exact)
    assert len(calls) == 1
    system = calls[0]["messages"][0]["content"]
    assert json.dumps(list(exact), ensure_ascii=False) in system
    assert "不是指令" in system
    assert "权威来源" in system


@pytest.mark.asyncio
@pytest.mark.parametrize(("expected", "changed"), [
    ("春日修护", "春日修复"),
    ("30 mL / ¥199", "30 mL / ￥199"),
    ("A B", "A\u00a0B"),
    (" 春日修护 ", "春日修护"),
    ("é", "e\u0301"),
    ("春日\n修护", "春日 修护"),
])
async def test_changed_exact_characters_trigger_one_repair(expected, changed):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return reply(with_text(changed if len(calls) == 1 else expected))

    result = await generate_plan(
        "生成海报", base_url="https://provider.example/v1", model="m", exact_text=(expected,),
        transport=httpx.MockTransport(provider),
    )
    assert len(calls) == 2
    assert result.text_layers[0].text == expected
    assert "不得漏掉、合并或改写任何字符" in calls[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_missing_exact_layer_triggers_repair_and_duplicate_count_is_preserved():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        return reply(with_text("SALE") if calls == 1 else with_text("SALE", "SALE"))

    result = await generate_plan(
        "左右各放一份相同文案", base_url="https://provider.example/v1", model="m",
        exact_text=("SALE", "SALE"), transport=httpx.MockTransport(provider),
    )
    assert calls == 2
    assert [layer.text for layer in result.text_layers] == ["SALE", "SALE"]


@pytest.mark.asyncio
async def test_explicit_exact_text_overrides_inferred_old_copy():
    result = await generate_plan(
        "标题是「旧标题」", base_url="https://provider.example/v1", model="m",
        exact_text=("新标题",), transport=httpx.MockTransport(lambda request: reply(with_text("新标题"))),
    )
    assert [layer.text for layer in result.text_layers] == ["新标题"]


@pytest.mark.asyncio
async def test_repeated_missing_exact_copy_fails_safely_after_one_repair():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        return reply(with_text())

    secret_copy = "private-user-copy-春日修护"
    with pytest.raises(PlannerError, match="未完整保留指定的精确文案") as error:
        await generate_plan(
            "生成海报", base_url="https://provider.example/v1", model="m",
            exact_text=(secret_copy,), transport=httpx.MockTransport(provider),
        )
    assert calls == 2
    assert secret_copy not in str(error.value)
    assert "未提交任何 ComfyUI 任务" in str(error.value)


@pytest.mark.asyncio
async def test_inferred_title_and_price_are_locked_not_only_the_request_field():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        texts = ("春日修复", "30 mL / ¥299") if calls == 1 else ("春日修护", "30 mL / ¥199")
        return reply(with_text(*texts))

    original = "生成海报，标题必须逐字是「春日修护」，下面写「30 mL / ¥199」。"
    result = await generate_plan(
        original, base_url="https://provider.example/v1", model="m",
        transport=httpx.MockTransport(provider),
    )
    assert calls == 2
    assert result.request == original
    assert [layer.text for layer in result.text_layers] == ["春日修护", "30 mL / ¥199"]


@pytest.mark.parametrize(("brief", "expected"), [
    ("标题必须是「春日修护」", ("春日修护",)),
    ("标题必须「春日修护」", ("春日修护",)),
    ("标题是“春日修护”，价格为‘¥199’", ("春日修护", "¥199")),
    ("主标题：『春日修护』，副标题写成「晨间护理」", ("春日修护", "晨间护理")),
    ("下面写「30 mL / ¥199」", ("30 mL / ¥199",)),
    ('标题是"Spring Care"', ("Spring Care",)),
    ("标题是'Men's Care'", ("Men's Care",)),
    (r'标题是"Spring \"Care\""', ('Spring "Care"',)),
    (r'标题是"C:\\new"', (r"C:\new",)),
    (r'标题是"line\nline"', (r"line\nline",)),
    ("标题是「春日\n修护」", ("春日\n修护",)),
    ("标题必须为「春日修护」，请确保标题是「春日修护」", ("春日修护",)),
    ("标题是「不要等」下面写「现在买」", ("不要等", "现在买")),
    ("标题是「内含『春日』二字」", ("内含『春日』二字",)),
    ("标题是「内含「春日」二字」", ("内含「春日」二字",)),
])
def test_conservative_rendered_copy_extraction(brief, expected):
    from comfyui_director.planner import _infer_exact_text

    assert _infer_exact_text(brief) == expected


@pytest.mark.parametrize("brief", [
    "风格像“清晨的呼吸”，颜色要有「温柔感」。",
    "标题风格是「极简」而不是复古。",
    "不要让标题写成「SALE」。",
    "例如标题是「SALE」，这里只讨论风格，不要求放字。",
    "下面不要写「SALE」。",
    "价格要有「惊喜感」。",
    "标题是「未闭合",
])
def test_ordinary_quotes_negation_and_examples_are_not_inferred_as_copy(brief):
    from comfyui_director.planner import _infer_exact_text

    assert _infer_exact_text(brief) == ()


@pytest.mark.asyncio
async def test_ordinary_quotes_do_not_force_a_text_layer():
    result = await generate_plan(
        "风格像“清晨的呼吸”，颜色要有「温柔感」。",
        base_url="https://provider.example/v1", model="m",
        transport=httpx.MockTransport(lambda request: reply(with_text())),
    )
    assert result.text_layers == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exact", [
    ("",), ("   ",), ("x" * 1001,), tuple("x" for _ in range(33)),
    tuple("x" * 1000 for _ in range(9)), (123,), ["text"],
])
async def test_exact_copy_limits_are_checked_before_a_model_request(exact):
    def provider(request):
        raise AssertionError("invalid constraints must not call the model")

    with pytest.raises(PlannerError, match="精确文案"):
        await generate_plan(
            "生成海报", base_url="https://provider.example/v1", model="m",
            exact_text=exact, transport=httpx.MockTransport(provider),
        )


@pytest.mark.asyncio
async def test_missing_distinct_price_layer_cannot_be_hidden_in_another_layer():
    calls = 0

    def provider(request):
        nonlocal calls
        calls += 1
        return reply(with_text("春日修护\n¥199") if calls == 1 else with_text("春日修护", "¥199"))

    result = await generate_plan(
        "生成海报", base_url="https://provider.example/v1", model="m",
        exact_text=("春日修护", "¥199"), transport=httpx.MockTransport(provider),
    )
    assert calls == 2
    assert [layer.text for layer in result.text_layers] == ["春日修护", "¥199"]
