"""Cancelable, timeout-bounded subprocess execution for media tools."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable


class MediaProcessError(RuntimeError):
    pass


class MediaProcessTimeout(MediaProcessError):
    pass


class MediaProcessCanceled(MediaProcessError):
    pass


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_process(
    command: list[str],
    *,
    timeout: float,
    cancel_check: Callable[[], bool] | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    if cancel_check and cancel_check():
        raise MediaProcessCanceled("视频质检已取消")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_check and cancel_check():
                _terminate(process)
                raise MediaProcessCanceled("视频质检已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise MediaProcessTimeout(f"媒体处理超时（{timeout:g} 秒）")
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            detail = stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr or "")
            raise MediaProcessError(detail.strip()[-1_000:] or f"媒体命令退出码 {process.returncode}")
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    finally:
        if process.poll() is None:
            _terminate(process)
