from pathlib import Path

from local_agent import agent


def test_windows_chrome_candidates_use_common_install_locations(monkeypatch):
    monkeypatch.setattr(agent.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMFILES", r"C:\Program Files")
    monkeypatch.setenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.setattr(agent.shutil, "which", lambda _: None)

    candidates = agent._chrome_candidates()

    assert str(Path(r"C:\Program Files") / "Google/Chrome/Application/chrome.exe") in candidates
    assert str(Path(r"C:\Program Files (x86)") / "Google/Chrome/Application/chrome.exe") in candidates
    assert str(Path(r"C:\Users\tester\AppData\Local") / "Google/Chrome/Application/chrome.exe") in candidates
