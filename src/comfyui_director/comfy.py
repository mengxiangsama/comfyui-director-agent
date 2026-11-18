"""Conservative HTTP adapter for a self-hosted ComfyUI server.

A failed submission is never retried automatically: the server may already have
queued it. History is always read by the exact returned prompt ID.
"""

from __future__ import annotations

import asyncio
import mimetypes
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class ComfyError(RuntimeError):
    """A worker operation failed; the message is safe to show to the caller."""


class SubmissionUncertain(ComfyError):
    """Submission may have reached ComfyUI; verify its queue before resubmitting."""


class ComfyExecutionFailed(ComfyError):
    """ComfyUI history confirms that a submitted job reached a terminal failure."""


_MODEL_FIELDS = frozenset({
    "ckpt_name", "unet_name", "clip_name", "clip_name1", "clip_name2", "clip_name3",
    "vae_name", "lora_name", "control_net_name", "model_name", "style_model_name",
    "upscale_model_name", "clip_vision_name", "gligen_name", "diffusion_model_name",
})


def _relative_name(value: Any, *, allow_folder: bool = False) -> str:
    """Accept only ComfyUI-relative artifact identifiers, never filesystem paths."""
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise ComfyError("Worker returned an invalid artifact identifier.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ComfyError("Worker returned an invalid artifact identifier.")
    if not allow_folder and (not value or "/" in value or value in (".", "..")):
        raise ComfyError("Worker returned an invalid artifact filename.")
    return value


class ComfyClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_artifact_mb: int = 256,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Worker URL must be http(s) without inline credentials or query.")
        if max_artifact_mb <= 0:
            raise ValueError("max_artifact_mb must be positive")
        self.max_artifact_bytes = max_artifact_mb * 1024 * 1024
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            transport=transport,
            timeout=30,
            follow_redirects=False,
            # Worker addresses describe direct endpoints, normally on the LAN.
            # Do not accidentally route private images/auth through an ambient proxy.
            trust_env=False,
        )

    async def __aenter__(self) -> ComfyClient:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.__aexit__(*exc)

    async def health(self) -> dict:
        try:
            response = await self._client.get("system_stats")
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise ComfyError("Worker health check failed.") from None
        if not isinstance(data, dict):
            raise ComfyError("Worker returned an invalid health response.")
        return data

    async def object_info(self) -> dict:
        try:
            response = await self._client.get("object_info")
            response.raise_for_status()
            available = response.json()
        except (httpx.HTTPError, ValueError):
            raise ComfyError("Could not read worker node capabilities.") from None
        if not isinstance(available, dict):
            raise ComfyError("Worker returned invalid node capabilities.")
        return available

    async def validate_workflow(self, workflow: dict, *, node_info: dict | None = None) -> None:
        if not isinstance(workflow, dict) or not workflow:
            raise ComfyError("Workflow must be a nonempty ComfyUI API-format object.")
        for node in workflow.values():
            if (not isinstance(node, dict) or not isinstance(node.get("class_type"), str)
                    or not isinstance(node.get("inputs"), dict)):
                raise ComfyError("Workflow must use ComfyUI API format, not editor format.")
        if node_info is None:
            node_info = await self.object_info()
        missing = sorted({node["class_type"] for node in workflow.values()} - node_info.keys())
        if missing:
            raise ComfyError("Worker is missing node classes: " + ", ".join(missing))

        problems = []
        for node_id, node in workflow.items():
            node_type = node["class_type"]
            schema = node_info.get(node_type)
            fields = schema.get("input", {}) if isinstance(schema, dict) else {}
            if not isinstance(fields, dict):
                continue
            values = node["inputs"]
            for group in ("required", "optional"):
                declared = fields.get(group, {})
                if not isinstance(declared, dict):
                    continue
                for name, definition in declared.items():
                    if name not in values:
                        if group == "required":
                            problems.append(f"{node_type}[{node_id}] is missing required input {name}")
                        continue
                    if not isinstance(definition, list) or not definition:
                        continue
                    kind = definition[0]
                    options = definition[1] if len(definition) > 1 and isinstance(definition[1], dict) else {}
                    # Upload-driven choices are populated at runtime. Dynamic input
                    # groups and graph links are validated by the worker on submission.
                    if any(options.get(key) for key in ("image_upload", "video_upload", "audio_upload", "remote")):
                        continue
                    if node_type == "LoadImage" and name == "image":
                        continue
                    choices = kind if isinstance(kind, list) else options.get("options") if kind == "COMBO" else None
                    value = values[name]
                    if isinstance(choices, list) and isinstance(value, (str, int, float, bool)) and value not in choices:
                        detail = f" {value!r}" if name in _MODEL_FIELDS else ""
                        problems.append(f"{node_type}[{node_id}] has unavailable {name}{detail}")
        if problems:
            raise ComfyError("; ".join(problems))

    async def submit(self, workflow: dict, client_id: str) -> str:
        try:
            response = await self._client.post(
                "prompt", json={"prompt": workflow, "client_id": client_id}
            )
        except httpx.HTTPError:
            raise SubmissionUncertain(
                "Worker submission response was lost. Check its queue/history before resubmitting."
            ) from None
        if 400 <= response.status_code < 500 and response.status_code != 408:
            raise ComfyError(f"Worker rejected workflow submission (HTTP {response.status_code}).")
        if not 200 <= response.status_code < 300:
            raise SubmissionUncertain(
                "Worker submission was not confirmed. Check its queue/history before resubmitting."
            )
        try:
            data = response.json()
        except ValueError:
            raise SubmissionUncertain(
                "Worker returned an unreadable submission response. Check its queue/history."
            ) from None
        if isinstance(data, dict) and data.get("error"):
            raise ComfyError("Worker rejected workflow validation.")
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("prompt_id"), str)
            or not data["prompt_id"]
        ):
            raise SubmissionUncertain(
                "Worker returned no prompt ID. Check its queue/history before resubmitting."
            )
        return data["prompt_id"]

    async def wait(
        self, prompt_id: str, *, poll_interval: float = 2, timeout: float = 1800
    ) -> dict:
        if not prompt_id or poll_interval <= 0 or timeout <= 0:
            raise ValueError("prompt_id, poll_interval, and timeout must be positive/nonempty")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ComfyError(
                    "Worker history wait timed out; the remote task may still be running."
                )
            try:
                async with asyncio.timeout(remaining):
                    response = await self._client.get(
                        f"history/{quote(prompt_id, safe='')}", timeout=min(30, remaining)
                    )
                # Polling is read-only. A transient worker/proxy failure is safe to retry.
                if response.status_code >= 500:
                    raise httpx.ReadError("transient history response")
                response.raise_for_status()
            except (httpx.TransportError, TimeoutError):
                await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))
                continue
            except httpx.HTTPStatusError:
                raise ComfyError("Worker rejected the history request.") from None
            try:
                history = response.json()
            except ValueError:
                raise ComfyError("Worker returned unreadable history.") from None
            if not isinstance(history, dict):
                raise ComfyError("Worker returned invalid history.")
            entry = history.get(prompt_id)
            if entry is not None:
                if not isinstance(entry, dict):
                    raise ComfyError("Worker returned an invalid history entry.")
                status = entry.get("status", {})
                if not isinstance(status, dict):
                    raise ComfyError("Worker returned invalid execution status.")
                messages = status.get("messages", [])
                for message in messages if isinstance(messages, list) else []:
                    if (
                        isinstance(message, (list, tuple))
                        and message
                        and message[0] in ("execution_error", "execution_interrupted")
                    ):
                        raise ComfyExecutionFailed("Worker workflow execution failed or was interrupted.")
                if status.get("status_str") in ("error", "failed", "interrupted"):
                    raise ComfyExecutionFailed("Worker workflow execution failed or was interrupted.")
                if status.get("completed") is True:
                    return entry
            await asyncio.sleep(min(poll_interval, max(0, deadline - time.monotonic())))

    async def upload(self, path: Path) -> str:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
            raise ComfyError("Only supported image files can be uploaded to ComfyUI.")
        upload_name = f"director-{uuid.uuid4().hex}{suffix}"
        media_type = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as file:
                response = await self._client.post(
                    "upload/image",
                    data={"type": "input", "overwrite": "false"},
                    files={"image": (upload_name, file, media_type)},
                )
            response.raise_for_status()
            data = response.json()
        except (OSError, httpx.HTTPError, ValueError):
            raise ComfyError("Could not upload the input image to the worker.") from None
        if not isinstance(data, dict):
            raise ComfyError("Worker returned an invalid upload response.")
        name = _relative_name(data.get("name"))
        subfolder = _relative_name(data.get("subfolder", ""), allow_folder=True)
        return f"{subfolder}/{name}" if subfolder else name

    async def download(self, output: dict, destination: Path) -> Path:
        if not isinstance(output, dict):
            raise ComfyError("Worker returned an invalid artifact descriptor.")
        filename = _relative_name(output.get("filename"))
        subfolder = _relative_name(output.get("subfolder", ""), allow_folder=True)
        output_type = output.get("type", "output")
        if output_type not in ("input", "output", "temp"):
            raise ComfyError("Worker returned an invalid artifact type.")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + f".{uuid.uuid4().hex}.partial")
        params = {"filename": filename, "subfolder": subfolder, "type": output_type}
        try:
            async with self._client.stream("GET", "view", params=params) as response:
                response.raise_for_status()
                size_header = response.headers.get("content-length")
                if size_header:
                    try:
                        declared_size = int(size_header)
                    except ValueError:
                        raise ComfyError("Worker returned an invalid artifact size.") from None
                    if declared_size > self.max_artifact_bytes:
                        raise ComfyError("Worker artifact exceeds the configured size limit.")
                total = 0
                with partial.open("xb") as file:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.max_artifact_bytes:
                            raise ComfyError("Worker artifact exceeds the configured size limit.")
                        file.write(chunk)
            partial.replace(destination)
            return destination
        except (httpx.HTTPError, OSError):
            raise ComfyError("Could not download the worker artifact.") from None
        finally:
            partial.unlink(missing_ok=True)
