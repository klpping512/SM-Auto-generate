#!/usr/bin/env python3
"""SA-LogiFlow 本地扫码助手：启动本机 Chrome 并回传登录 Cookie。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright


VERSION = "0.1.0"
HOST = "127.0.0.1"
DEFAULT_PORT = 18765
MAX_CONCURRENT_SESSIONS = 3
logger = logging.getLogger("salogiflow-agent")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    origin = handler.headers.get("Origin", "")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", origin or "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Vary", "Origin")
    handler.end_headers()
    handler.wfile.write(body)


def _valid_url(value: str) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _chrome_candidates() -> list[str]:
    if sys.platform == "win32":
        program_files = os.environ.get("PROGRAMFILES", r"C:\\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        return [
            str(Path(program_files) / "Google/Chrome/Application/chrome.exe"),
            str(Path(program_files_x86) / "Google/Chrome/Application/chrome.exe"),
            str(Path(local_app_data) / "Google/Chrome/Application/chrome.exe") if local_app_data else "",
            shutil.which("chrome.exe"),
            shutil.which("chrome"),
        ]
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
        ]
    return [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]


def _chrome_executable() -> str | None:
    return next((candidate for candidate in _chrome_candidates() if candidate and Path(candidate).exists()), None)


def _post_json(url: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


async def _run_browser_session(session: dict):
    completion_url = urljoin(session["server_url"].rstrip("/") + "/", session["complete_path"].lstrip("/"))
    profile_dir = Path(tempfile.mkdtemp(prefix=f"salogiflow-{session['session_id'][:8]}-"))
    try:
        async with async_playwright() as playwright:
            launch_args = ["--no-first-run", "--no-default-browser-check", "--disable-popup-blocking"]
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel="chrome",
                headless=False,
                args=launch_args,
            )
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(session["login_url"], timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_selector(session["logged_in_selector"], timeout=180000)
                cookies = await context.cookies()
                if not cookies:
                    raise RuntimeError("浏览器已检测到登录，但没有获取到 Cookie")
                _post_json(completion_url, {
                    "handoff_token": session["handoff_token"],
                    "cookies": cookies,
                })
            finally:
                await context.close()
    except Exception as exc:
        logger.exception("本地扫码会话失败: %s", session["session_id"])
        try:
            _post_json(completion_url, {
                "handoff_token": session["handoff_token"],
                "cookies": [],
                "error": str(exc),
            })
        except Exception:
            logger.exception("无法回传本地扫码失败状态")
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)


class AgentState:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, threading.Thread] = {}

    def start(self, session: dict) -> bool:
        with self.lock:
            self.sessions = {key: value for key, value in self.sessions.items() if value.is_alive()}
            if len(self.sessions) >= MAX_CONCURRENT_SESSIONS or session["session_id"] in self.sessions:
                return False
            thread = threading.Thread(
                target=lambda: asyncio.run(_run_browser_session(session)),
                name=f"salogiflow-scan-{session['session_id'][:8]}",
                daemon=True,
            )
            self.sessions[session["session_id"]] = thread
            thread.start()
            return True


class AgentHandler(BaseHTTPRequestHandler):
    server: "AgentHTTPServer"

    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def do_OPTIONS(self):
        _json_response(self, 204, {})

    def do_GET(self):
        if self.path != "/health":
            _json_response(self, 404, {"ok": False, "error": "Not found"})
            return
        _json_response(self, 200, {
            "ok": True,
            "service": "salogiflow-agent",
            "version": VERSION,
            "platform": sys.platform,
            "chrome": bool(_chrome_executable()),
        })

    def do_POST(self):
        if self.path != "/v1/sessions":
            _json_response(self, 404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            _json_response(self, 400, {"ok": False, "error": "请求格式错误"})
            return
        required = ("server_url", "session_id", "handoff_token", "complete_path", "login_url", "logged_in_selector")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            _json_response(self, 400, {"ok": False, "error": f"缺少字段: {', '.join(missing)}"})
            return
        if not _valid_url(payload["server_url"]) or not _valid_url(payload["login_url"]):
            _json_response(self, 400, {"ok": False, "error": "URL 不合法"})
            return
        if not _chrome_executable():
            _json_response(self, 424, {"ok": False, "error": "未找到本机 Google Chrome"})
            return
        if not self.server.state.start(payload):
            _json_response(self, 409, {"ok": False, "error": "本机已有扫码任务，请稍后重试"})
            return
        _json_response(self, 202, {"ok": True, "started": True, "session_id": payload["session_id"]})


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.state = AgentState()


def run_server(port: int = DEFAULT_PORT):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server = AgentHTTPServer((HOST, port), AgentHandler)
    logger.info("SA-LogiFlow 本地助手已启动: http://%s:%s", HOST, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server(int(os.environ.get("SALOGIFLOW_AGENT_PORT", str(DEFAULT_PORT))))
