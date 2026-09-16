"""Local model lane: an OpenAI compatible chat endpoint on loopback (Ollama by default).

The request runs on a background thread inside the server process, and the thread leaves
the same files a subprocess job leaves (`stdout.txt`, `stderr.txt`, `exit_code.txt`), so
poll and collect treat both kinds of job the same way. Only loopback hosts are accepted
and redirects are refused, so a misconfigured URL cannot send a prompt off the machine.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from olondunge.jobs import store
from olondunge.lanes import process
from olondunge.lanes.base import Brief, Health, JobId
from olondunge.redact import redact

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.1:8b"
HEALTH_TIMEOUT_S = 3.0
RESULT_FILE = "local_result.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _base_url() -> str:
    return os.environ.get("OLONDUNGE_LOCAL_BASE_URL", DEFAULT_BASE_URL).strip()


class LocalLane:
    name = "local"

    def __init__(self, base_url: str | None = None) -> None:
        url = base_url or _base_url()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS:
            raise ValueError("the local lane accepts only http://127.0.0.1, localhost or [::1]")
        self.host = parsed.hostname
        self.port = parsed.port or 11434
        host = f"[{self.host}]" if self.host == "::1" else self.host
        self.origin = f"http://{host}:{self.port}"

    def _call(self, path: str, data: bytes | None, timeout_s: float) -> tuple[int, str]:
        request = urllib.request.Request(
            self.origin + path, data=data, method="POST" if data is not None else "GET"
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        key = os.environ.get("OLONDUNGE_LOCAL_API_KEY")
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=timeout_s) as response:
                return int(response.status), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace") if exc.fp is not None else ""
            return int(exc.code), body

    def health(self) -> Health:
        try:
            socket.create_connection((self.host, self.port), timeout=HEALTH_TIMEOUT_S).close()
        except OSError:
            return Health(up=False, reason=f"nothing listening on {self.origin}", binary=None)
        try:
            status, body = self._call("/v1/models", None, HEALTH_TIMEOUT_S)
            json.loads(body)
        except (OSError, ValueError):
            return Health(up=False, reason="no OpenAI compatible /v1/models", binary=None)
        if not 200 <= status < 300:
            return Health(up=False, reason=f"/v1/models returned http {status}", binary=None)
        return Health(up=True, reason="OpenAI compatible endpoint found", binary=self.origin)

    def submit(self, brief: Brief) -> JobId:
        job_dir = store.create_job_dir()
        timeout_s = process.timeout_of(brief)
        model = brief.model or os.environ.get("OLONDUNGE_LOCAL_MODEL", DEFAULT_MODEL)
        store.begin_job(
            job_dir,
            lane=self.name,
            worker=self.name,
            kind=brief.kind,
            model=model,
            effort=None,
            timeout_s=timeout_s,
            argv=[f"POST {self.origin}/v1/chat/completions"],
            run_cwd=str(brief.cwd),
        )
        store.update_job(job_dir, status="running", pid=None, in_process=True)
        thread = threading.Thread(
            target=self._run, args=(job_dir, brief.prompt, model, timeout_s), daemon=True
        )
        thread.start()
        return JobId(value=job_dir.name, job_dir=job_dir, worker=self.name)

    def _run(self, job_dir: Path, prompt: str, model: str, timeout_s: float) -> None:
        payload = json.dumps(
            {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False}
        ).encode("utf-8")
        code = 0
        stdout = ""
        stderr = ""
        try:
            status, body = self._call("/v1/chat/completions", payload, timeout_s)
            stdout = body
            if not 200 <= status < 300:
                stderr, code = f"http {status}", 1
        except (OSError, ValueError) as exc:
            stderr, code = f"{type(exc).__name__}: {exc}", 1
            if "timed out" in str(exc).lower():
                (job_dir / "timed_out.txt").write_text(str(timeout_s), encoding="utf-8")
                code = 124
        (job_dir / "stdout.txt").write_text(redact(stdout), encoding="utf-8")
        (job_dir / "stderr.txt").write_text(redact(stderr), encoding="utf-8")
        (job_dir / "exit_code.txt").write_text(str(code), encoding="utf-8")


def parse(job_dir: Path, stdout: str, stderr: str) -> tuple[str, dict[str, Any] | None, list[str]]:
    del job_dir, stderr
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout.strip(), None, ["local endpoint returned non JSON"]
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "", None, ["local endpoint returned no completion"]
    usage_raw = data.get("usage") if isinstance(data, dict) else None
    usage: dict[str, Any] | None = None
    if isinstance(usage_raw, dict):
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens"),
            "output_tokens": usage_raw.get("completion_tokens"),
        }
    return str(content).strip(), usage, []
