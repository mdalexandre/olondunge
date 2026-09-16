"""Session leader for one worker: launch it, redact its transcripts as they stream, enforce
the timeout, and record the exit code.

The wrap exists so a job outlives the tool call that started it and still leaves a
complete, redacted record on disk. `exit_code.txt` is written last, so its presence is the
single signal that the worker is finished; poll and collect key on it.

Usage: python -m olondunge.jobs.wrap --job-dir DIR --timeout-s N -- worker argv...
"""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO

from olondunge.redact import redact

EXIT_BAD_ARGS = 2
EXIT_TIMEOUT = 124
DRAIN_S = 2.0


# Transcripts are redacted line by line as they stream. Re-redacting the whole transcript on
# every chunk was quadratic (OBSERVED: 18 s of overhead for a 2 MB transcript). A private key
# block is the one secret that spans lines, so an open `-----BEGIN` is held back until its
# `-----END` arrives or the held text passes HOLD_LIMIT.
HOLD_LIMIT = 1 << 20
TAIL_KEEP = 4096


def _flush_point(buffer: str) -> int:
    """How much of `buffer` can be redacted and written now without splitting a secret."""

    cut = buffer.rfind("\n") + 1
    begin = buffer.rfind("-----BEGIN", 0, cut)
    if begin != -1 and buffer.find("-----END", begin, cut) == -1 and len(buffer) <= HOLD_LIMIT:
        cut = buffer.rfind("\n", 0, begin) + 1
    if cut == 0 and len(buffer) > HOLD_LIMIT:
        # One enormous line: write most of it, keeping a tail that may hold a split token.
        cut = len(buffer) - TAIL_KEEP
    return cut


def _pump(src: IO[bytes], dest: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    with dest.open("w", encoding="utf-8", errors="replace") as out:
        while True:
            chunk = src.read1(65536) if hasattr(src, "read1") else src.read(65536)
            if not chunk:
                break
            pending += decoder.decode(chunk)
            cut = _flush_point(pending)
            if cut:
                out.write(redact(pending[:cut]))
                out.flush()
                pending = pending[cut:]
        pending += decoder.decode(b"", final=True)
        if pending:
            out.write(redact(pending))
            out.flush()


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in args:
        print("wrap: missing -- before the worker argv", file=sys.stderr)
        return EXIT_BAD_ARGS
    split = args.index("--")
    head, worker_argv = args[:split], args[split + 1 :]
    job_dir: Path | None = None
    timeout_s: float | None = None
    for i, token in enumerate(head):
        if token == "--job-dir" and i + 1 < len(head):
            job_dir = Path(head[i + 1])
        if token == "--timeout-s" and i + 1 < len(head):
            try:
                timeout_s = float(head[i + 1])
            except ValueError:
                timeout_s = None
    if job_dir is None or not job_dir.is_dir() or timeout_s is None or not worker_argv:
        print("wrap: need --job-dir DIR, --timeout-s N and a worker argv", file=sys.stderr)
        return EXIT_BAD_ARGS

    stdout_path = job_dir / "stdout.txt"
    stderr_path = job_dir / "stderr.txt"
    exit_path = job_dir / "exit_code.txt"
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            worker_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        stderr_path.write_text(redact(f"wrap: worker launch failed: {exc}\n"), encoding="utf-8")
        exit_path.write_text("127", encoding="utf-8")
        return 127

    assert proc.stdout is not None and proc.stderr is not None
    pumps = [
        threading.Thread(target=_pump, args=(proc.stdout, stdout_path), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, stderr_path), daemon=True),
    ]
    for pump in pumps:
        pump.start()
    timed_out = False
    try:
        code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        code = EXIT_TIMEOUT
    deadline = time.monotonic() + DRAIN_S
    for pump in pumps:
        pump.join(timeout=max(0.0, deadline - time.monotonic()))
    if timed_out:
        (job_dir / "timed_out.txt").write_text(str(timeout_s), encoding="utf-8")
        with stderr_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\ntimeout after {timeout_s}s\n")
    exit_path.write_text(str(code), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
