from __future__ import annotations

import subprocess

from ob_bridge.health import _safe_parse_launch_command, launch_ob


def test_safe_parse_launch_command_accepts_normal_argv():
    assert _safe_parse_launch_command("openbiliclaw serve --port 8420") == [
        "openbiliclaw",
        "serve",
        "--port",
        "8420",
    ]
    assert _safe_parse_launch_command('"C:/Program Files/OpenBiliClaw/ob" serve') == [
        "C:/Program Files/OpenBiliClaw/ob",
        "serve",
    ]


def test_safe_parse_launch_command_rejects_invalid_input():
    assert _safe_parse_launch_command("") is None
    assert _safe_parse_launch_command("   ") is None
    assert _safe_parse_launch_command('openbiliclaw "unterminated') is None
    assert _safe_parse_launch_command("openbiliclaw serve\nwhoami") is None
    assert _safe_parse_launch_command("openbiliclaw serve\x00whoami") is None


def test_launch_ob_never_invokes_a_shell_for_configured_command(monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert launch_ob("", "openbiliclaw serve; touch /tmp/PWNED_MARKER") is True

    assert captured["command"] == ["openbiliclaw", "serve;", "touch", "/tmp/PWNED_MARKER"]
    assert captured["shell"] is False


def test_launch_ob_rejects_control_character_injection(monkeypatch):
    called = False

    def fake_popen(_command, **_kwargs):
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    assert launch_ob("", "openbiliclaw serve\nwhoami") is False
    assert called is False
