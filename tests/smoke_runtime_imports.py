"""Real business import smoke; run explicitly with full runtime dependencies.

python -B tests/smoke_runtime_imports.py plugin missing
python -B tests/smoke_runtime_imports.py standalone existing
All account/network data is synthetic and all writes are temporary.
"""
import asyncio
import builtins
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1]
MODE = sys.argv[1]
STATE = sys.argv[2]
if MODE not in {"plugin", "standalone"} or STATE not in {"missing", "existing"}:
    raise SystemExit("Expected plugin|standalone and missing|existing")

with tempfile.TemporaryDirectory(prefix="bili-import-readonly-") as temporary, ExitStack() as stack:
    root = Path(temporary).resolve()
    profile = root / "profile"
    stack.enter_context(patch.dict(os.environ, {
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "LOCALAPPDATA": str(root / "local"), "BILI_USER_DATA_DIR": str(profile),
    }))
    for key in tuple(os.environ):
        if key.startswith("BILI_") and key != "BILI_USER_DATA_DIR":
            del os.environ[key]
    stack.enter_context(patch.object(Path, "home", return_value=root / "home"))
    stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
    config_file = profile / "Data" / "config.json"
    before = {}
    if STATE == "existing":
        config_file.parent.mkdir(parents=True)
        config_file.write_text(json.dumps({
            "api": {"model_brain": "existing-test-model", "unified_api_key": "synthetic-key"},
            "knowledge_base_dir": "notes/nested",
            "reply_safety": {"blocked_keywords": ["ciphertext-without-a-key"]},
        }), encoding="utf-8")
        before = {p.relative_to(profile): p.read_bytes() for p in profile.rglob("*") if p.is_file()}

    # Windows event loops use a local socketpair, not an outbound request.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stack.callback(loop.close)
    stack.callback(asyncio.set_event_loop, None)
    for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
        stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden in import smoke")))

    logger = SimpleNamespace(**{name: Mock() for name in ("debug", "info", "warning", "error", "critical")})
    astrbot, api, event, star = [ModuleType(name) for name in
                               ("astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star")]
    astrbot.__path__ = api.__path__ = []
    api.logger = logger
    event.AstrMessageEvent = object
    event.filter = SimpleNamespace(command=lambda name: lambda fn: fn)
    star.Context = object
    star.Star = type("Star", (), {"__init__": lambda self, context: None})
    get_data_dir = Mock(return_value=profile)
    star.StarTools = SimpleNamespace(get_data_dir=get_data_dir)
    stack.enter_context(patch.dict(sys.modules, {m.__name__: m for m in (astrbot, api, event, star)}))
    prefix = "_readonly_import_smoke"
    spec = importlib.util.spec_from_file_location(prefix, SOURCE / "__init__.py", submodule_search_locations=[str(SOURCE)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[prefix] = package
    spec.loader.exec_module(package)
    runtime = importlib.import_module(prefix + "._runtime")
    if MODE == "standalone":
        runtime.configure_standalone()

    original_path = list(sys.path)
    stdout, stderr = io.StringIO(), io.StringIO()
    with ExitStack() as guards:
        guards.enter_context(redirect_stdout(stdout))
        guards.enter_context(redirect_stderr(stderr))
        original_stdout, original_stderr = sys.stdout, sys.stderr
        for owner in (builtins, io):
            original_open = owner.open

            def read_only_open(file, mode="r", *args, _open=original_open, **kwargs):
                if any(flag in mode for flag in "wax+"):
                    raise AssertionError("Import attempted file write")
                return _open(file, mode, *args, **kwargs)

            guards.enter_context(patch.object(owner, "open", new=read_only_open))
        original_os_open = os.open

        def read_only_os_open(path, flags, *args, **kwargs):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                raise AssertionError("Import attempted low-level file write")
            return original_os_open(path, flags, *args, **kwargs)

        guards.enter_context(patch.object(os, "open", new=read_only_os_open))
        for method in ("mkdir", "makedirs", "replace", "remove", "unlink", "rmdir", "chmod"):
            guards.enter_context(patch.object(os, method, side_effect=AssertionError(f"Import called os.{method}")))
        guards.enter_context(patch.object(Path, "mkdir", side_effect=AssertionError("Import called Path.mkdir")))
        if MODE == "plugin":
            main = importlib.import_module(prefix + ".main")
            main.BilibiliLearningBot(None, {})
            get_data_dir.assert_called_once_with(runtime.PLUGIN_NAME)
        core = importlib.import_module(prefix + ".core.config")
        config_reference = core.config
        cli = importlib.import_module(prefix + ".cli.app")
        cli_reference = cli.config
        brain = importlib.import_module(prefix + ".brain.agent_brain")
        assert callable(brain.AgentBrain)
        assert core.load_config()["api"]["model_brain"] == ("existing-test-model" if STATE == "existing" else "")
        assert sys.stdout is original_stdout and sys.stderr is original_stderr
    assert stdout.getvalue() == "", stdout.getvalue()
    assert stderr.getvalue() == "", stderr.getvalue()
    if MODE == "standalone":
        get_data_dir.assert_not_called()
    assert not (profile / ".cipher_key").exists()
    assert not (profile / "notes").exists()
    if STATE == "missing":
        assert not profile.exists(), "Business import created profile"
    else:
        after = {p.relative_to(profile): p.read_bytes() for p in profile.rglob("*") if p.is_file()}
        assert after == before
    assert sys.path == original_path

    # Explicit initialization must still work and keep every exported dict alive.
    terminal = Mock()
    with patch.object(cli, "_initialize_cli_terminal", new=terminal):
        cli.initialize_cli()
    terminal.assert_called_once_with()
    assert core.config is config_reference
    assert cli.config is cli_reference
    assert config_file.is_file()
    expected_model = "existing-test-model" if STATE == "existing" else ""
    assert core.config["api"]["model_brain"] == cli.config["api"]["model_brain"] == expected_model
    assert not (root / "home").exists()
    assert not (root / "local").exists()
    saved = config_file.read_bytes()
    core.initialize_config()
    assert config_file.read_bytes() == saved
    if STATE == "existing":
        assert config_file.read_bytes() == before[Path("Data/config.json")]

    permissions = importlib.import_module(prefix + ".core.action_permissions")
    bridge = importlib.import_module(prefix + ".core.astrbot_config_bridge")
    assert cli.DEFAULT_CONFIG is not core.DEFAULT_CONFIG
    assert cli.DEFAULT_CONFIG["ai_permissions"] is not core.DEFAULT_CONFIG["ai_permissions"]
    assert not permissions.allowed("video_like")
    with patch.object(cli, "_reload_all_globals", wraps=cli._reload_all_globals) as reload_globals:
        bridge.apply_astrbot_config({
            "use_astrbot_config": True, "apply_sections": ["ai_permissions"],
            "ai_permissions": {"enabled": True, "actions": {"video_like": True}},
        })
        reload_globals.assert_called_once_with(cli.config)
    assert permissions.allowed("video_like")
    assert not permissions.allowed("coin")
    assert cli.config["ai_permissions"] == core.config["ai_permissions"]
    bridge.apply_astrbot_config({
        "use_astrbot_config": True, "apply_sections": ["ai_permissions"],
        "ai_permissions": {"enabled": False},
    })
    assert not permissions.allowed("video_like")
    assert core.config is config_reference and cli.config is cli_reference
    assert core.config["api"]["model_brain"] == expected_model
    assert sys.path == original_path
    print(f"PASS: real {MODE} business import ({STATE} profile) is read-only; explicit initialization, shared references and live permissions work.")
