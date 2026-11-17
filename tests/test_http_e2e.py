"""Run the actual CLI against three loopback HTTP worker simulators; no GPU/model."""

import io
import json
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image


def test_cli_three_http_workers_compose_and_resume_without_resubmitting(tmp_path, monkeypatch):
    # A broken ambient proxy must not intercept directly configured worker endpoints.
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(variable, "socks5://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    submissions = []
    artifacts = {}

    class WorkerHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, content_type="application/json"):
            body = json.dumps(data).encode() if content_type == "application/json" else data
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/system_stats":
                self.respond({"devices": []})
            elif self.path == "/object_info":
                self.respond({"FixtureGenerate": {}, "SaveImage": {}})
            elif self.path.startswith("/history/"):
                prompt_id = self.path.rsplit("/", 1)[1]
                self.respond({prompt_id: {"status": {"completed": True}, "outputs": {
                    "9": {"images": [{"filename": prompt_id + ".png", "subfolder": "", "type": "output"}]}
                }}})
            elif self.path.startswith("/view?"):
                from urllib.parse import parse_qs, urlsplit

                filename = parse_qs(urlsplit(self.path).query)["filename"][0]
                self.respond(artifacts[filename], "image/png")
            else:
                self.send_error(404)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = request["prompt"]["1"]["inputs"]["text"]
            prompt_id = uuid.uuid4().hex
            submissions.append((self.server.server_port, prompt_id))
            image = Image.new("RGBA", (64, 64), "blue") if text == "background" else Image.new("RGBA", (8, 8), (255, 0, 0, 254) if text == "subject" else (0, 255, 0, 254))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            artifacts[prompt_id + ".png"] = buffer.getvalue()
            self.respond({"prompt_id": prompt_id})

    servers = [ThreadingHTTPServer(("127.0.0.1", 0), WorkerHandler) for _ in range(3)]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def prompt(text):
            return {"positive_zh": text, "positive_en": text, "acceptance": ["visible"]}

        def placement(x, y, width=24, height=24):
            return {"x": x, "y": y, "width": width, "height": height, "instructions": "alpha", "acceptance": ["placed"]}

        plan = {
            "title": "loopback test", "request": "background and two subjects", "canvas": {"width": 64, "height": 64},
            "visual": dict.fromkeys(("style", "palette", "lighting", "camera", "composition"), "shared"),
            "full_image_prompt": prompt("all"), "background": prompt("background"), "foreground": prompt("subject"),
            "placement": placement(4, 4), "extra_layers": [{"id": "decoration", "prompt": prompt("green"), "placement": placement(36, 36)}],
        }
        graph = {"1": {"class_type": "FixtureGenerate", "inputs": {"text": ""}}, "9": {"class_type": "SaveImage", "inputs": {}}}
        profile = {"file": "workflow.json", "bindings": {"prompt": ["1", "text"]}, "outputs": {"image": {"node": "9"}}}
        config = {"workers": [{"id": f"worker-{i}", "url": f"http://127.0.0.1:{s.server_port}", "roles": ["background", "foreground"]} for i, s in enumerate(servers)], "profiles": {"background": profile, "foreground": profile}, "poll_interval_seconds": 0.01}
        for name, data in (("plan.json", plan), ("cluster.json", config), ("workflow.json", graph)):
            (tmp_path / name).write_text(json.dumps(data))
        run_dir = tmp_path / "run"
        command = [sys.executable, "-m", "comfyui_director", "run", str(tmp_path / "plan.json"), "--config", str(tmp_path / "cluster.json"), "--run-dir", str(run_dir)]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, timeout=60)
            assert result.returncode == 0, result.stderr.decode()
        assert len(submissions) == 3
        assert len({port for port, _ in submissions}) == 3
        assert json.loads((run_dir / "run.json").read_text())["state"] == "completed"
        with Image.open(run_dir / "final.png") as final:
            assert final.getpixel((0, 0)) == (0, 0, 255)
            assert final.getpixel((10, 10))[0] > 250
            assert final.getpixel((40, 40))[1] > 250
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
