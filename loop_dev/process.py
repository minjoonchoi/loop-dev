"""No shell interpolation, no interactive stdin, and bounded process groups."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time


def execute(argv, cwd, log_dir, timeout, prompt=None):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    env = dict(os.environ, CI="true", GIT_TERMINAL_PROMPT="0", PIP_NO_INPUT="1")
    result = {"argv": argv, "exit_code": None, "timed_out": False,
              "stdout": str(log_dir / "stdout.log"), "stderr": str(log_dir / "stderr.log")}
    with (log_dir / "stdout.log").open("wb") as out, (log_dir / "stderr.log").open("wb") as err:
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                                       stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
                                       stdout=out, stderr=err)
            try:
                process.communicate(prompt.encode() if prompt is not None else None, timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                terminate(process)
            except BaseException:
                terminate(process)
                raise
            result["exit_code"] = process.returncode
        except OSError as exc:
            result["error"] = str(exc)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def terminate(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # A child may still live even when its parent exits after SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
