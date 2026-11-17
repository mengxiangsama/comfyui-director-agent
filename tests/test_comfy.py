import json

import httpx
import pytest

from comfyui_director.comfy import ComfyClient, ComfyError, SubmissionUncertain


async def test_submission_and_history_are_bound_to_exact_prompt_id():
    seen = []
    workflow = {"9": {"class_type": "SaveImage", "inputs": {}}}

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/prompt":
            assert json.loads(request.content) == {"prompt": workflow, "client_id": "test-run"}
            assert request.headers["authorization"] == "Bearer secret-key"
            return httpx.Response(200, json={"prompt_id": "owned-task"})
        assert request.url.path == "/api/history/owned-task"
        return httpx.Response(
            200,
            json={
                "other-task": {"status": {"completed": True}, "outputs": {"wrong": {}}},
                "owned-task": {"status": {"completed": True}, "outputs": {"9": {}}},
            },
        )

    async with ComfyClient(
        "http://worker/api", api_key="secret-key", transport=httpx.MockTransport(handler)
    ) as client:
        prompt_id = await client.submit(workflow, "test-run")
        result = await client.wait(prompt_id, poll_interval=0.001, timeout=1)
    assert result["outputs"] == {"9": {}}
    assert len(seen) == 2


async def test_execution_error_is_reported_without_remote_body():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "p": {
                    "status": {
                        "completed": False,
                        "status_str": "error",
                        "messages": [["execution_error", {"exception_message": "SECRET"}]],
                    }
                }
            },
        )

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ComfyError, match="failed") as caught:
            await client.wait("p", poll_interval=0.001, timeout=1)
    assert "SECRET" not in str(caught.value)


async def test_interrupted_event_prevents_success_even_if_completed_flag_is_true():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "p": {
                    "status": {
                        "completed": True,
                        "messages": [["execution_interrupted", {}]],
                    }
                }
            },
        )
    )
    async with ComfyClient("http://worker", transport=transport) as client:
        with pytest.raises(ComfyError, match="interrupted"):
            await client.wait("p", poll_interval=0.001, timeout=1)


async def test_history_transient_transport_failure_retries_only_get():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("sensitive proxy detail", request=request)
        return httpx.Response(200, json={"p": {"status": {"completed": True}}})

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        await client.wait("p", poll_interval=0.001, timeout=1)
    assert [request.method for request in requests] == ["GET", "GET"]


async def test_history_timeout_does_not_submit_or_interrupt_remote_job():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={})

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ComfyError, match="may still be running"):
            await client.wait("p", poll_interval=0.002, timeout=0.005)
    assert methods
    assert set(methods) == {"GET"}


async def test_submit_timeout_never_retries_and_hides_secrets():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("sensitive-secret", request=request)

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(SubmissionUncertain) as caught:
            await client.submit({"9": {"class_type": "SaveImage", "inputs": {}}}, "c")
    assert calls == 1
    assert "sensitive-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (400, {"error": "credential-leak"}, ComfyError),
        (500, {"error": "credential-leak"}, SubmissionUncertain),
        (200, {}, SubmissionUncertain),
        (200, {"error": "credential-leak"}, ComfyError),
    ],
)
async def test_submit_failures_are_classified_without_response_body(status, body, error):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    async with ComfyClient("http://worker", transport=transport) as client:
        with pytest.raises(error) as caught:
            await client.submit({}, "c")
    assert type(caught.value) is error
    assert "credential-leak" not in str(caught.value)


async def test_submit_non_json_success_is_uncertain():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="secret HTML"))
    async with ComfyClient("http://worker", transport=transport) as client:
        with pytest.raises(SubmissionUncertain):
            await client.submit({}, "c")


async def test_validate_workflow_checks_node_classes():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"SaveImage": {"input": {}}})
    )
    async with ComfyClient("http://worker", transport=transport) as client:
        await client.validate_workflow({"9": {"class_type": "SaveImage", "inputs": {}}})
        with pytest.raises(ComfyError, match="missing node"):
            await client.validate_workflow({"9": {"class_type": "AbsentNode", "inputs": {}}})
        with pytest.raises(ComfyError, match="editor format"):
            await client.validate_workflow({"nodes": []})


async def test_upload_uses_unique_name_and_preserves_returned_subfolder(tmp_path):
    path = tmp_path / "input.png"
    path.write_bytes(b"image")
    names = []

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/upload/image"
        body = request.content.decode()
        assert 'filename="input.png"' not in body
        uploaded_name = body.split('filename="', 1)[1].split('"', 1)[0]
        names.append(uploaded_name)
        return httpx.Response(200, json={"name": uploaded_name, "subfolder": "director/run"})

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        first = await client.upload(path)
        second = await client.upload(path)
    assert first.startswith("director/run/director-")
    assert second != first
    assert len(set(names)) == 2


async def test_download_saves_only_to_explicit_destination(tmp_path):
    target = tmp_path / "artifacts" / "controlled.png"

    def handler(request):
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "worker-output.png"
        assert request.url.params["subfolder"] == "batch/17"
        return httpx.Response(200, content=b"pixels")

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        result = await client.download(
            {"filename": "worker-output.png", "subfolder": "batch/17", "type": "output"}, target
        )
    assert result == target
    assert target.read_bytes() == b"pixels"
    assert not list(tmp_path.rglob("*.partial"))


@pytest.mark.parametrize("declared", [True, False])
async def test_download_size_limit_removes_partial_file(tmp_path, declared):
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 700_000
            yield b"x" * 700_000

    def handler(request):
        headers = {"Content-Length": "1400000"} if declared else {}
        return httpx.Response(200, headers=headers, stream=Chunks())

    target = tmp_path / "result.png"
    async with ComfyClient(
        "http://worker", max_artifact_mb=1, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ComfyError, match="size limit"):
            await client.download({"filename": "image.png"}, target)
    assert not target.exists()
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize(
    "descriptor",
    [
        {"filename": "../secret"},
        {"filename": "safe.png", "subfolder": "../../secret"},
        {"filename": "/secret"},
        {"filename": "safe.png", "subfolder": "..\\secret"},
    ],
)
async def test_download_rejects_remote_path_traversal(tmp_path, descriptor):
    def handler(request):
        pytest.fail("No HTTP request should be made for an unsafe artifact descriptor")

    async with ComfyClient("http://worker", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ComfyError, match="invalid artifact"):
            await client.download(descriptor, tmp_path / "result")


@pytest.mark.parametrize(
    "url",
    ["ftp://worker", "http://user:secret@worker", "http://worker?token=secret"],
)
def test_base_url_never_accepts_inline_credentials(url):
    with pytest.raises(ValueError, match="without inline credentials"):
        ComfyClient(url)


async def test_health_and_rejection_do_not_echo_sensitive_error_body():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, text="secret-token"))
    async with ComfyClient("http://worker", transport=transport) as client:
        with pytest.raises(ComfyError) as caught:
            await client.health()
    assert "secret-token" not in str(caught.value)
