"""Platform adapter regressions: fake network/downloads and a temporary plugin root.

Run: python -B -m unittest discover -s tests -p test_async_platform.py -v
The real asyncio.to_thread wrappers run against gated synchronous fake I/O.
"""
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import importlib.util
import io
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_async_platform_test_plugin"
BV = "BV1Q541167Qg"
SHORT = "https://b23.tv/synthetic"
DIRECT = f"https://www.bilibili.com/video/{BV}"
LONG = DIRECT + "?p=3&source=b23.tv"
SECRET = "synthetic-raw-response cookie=synthetic-cookie api_key=synthetic-key"
PROXY = "http://synthetic-user:synthetic-password@proxy.invalid:8080"
INFO = {"id": "synthetic-id", "title": "Synthetic title", "duration": 12.5, "ext": "m4a"}
# Watchdogs only prevent a broken test hanging; no assertion counts timed ticks.
WATCHDOG = 5


class FakeHTTPError(Exception):
    pass


class FakeResponse:
    def __init__(self, url=LONG, status=200):
        self.url = url
        self.status_code = status
        self.status_checks = 0
        self.close_calls = 0
        self.exit_type = None
        self.closed = threading.Event()

    @property
    def text(self):
        raise AssertionError("short-link resolution must not read a response body")

    @property
    def content(self):
        raise AssertionError("short-link resolution must not download a response body")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_type = exc_type
        self.close()

    def close(self):
        self.close_calls += 1
        self.closed.set()

    def raise_for_status(self):
        self.status_checks += 1
        if self.status_code >= 400:
            raise FakeHTTPError(SECRET + " " + PROXY)


class FakeHTTP:
    def __init__(self):
        self.effects = {"head": [], "get": []}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs, threading.get_ident()))
        if not self.effects[method]:
            raise AssertionError("unexpected fake HTTP request")
        effect = self.effects[method].pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect() if callable(effect) else effect

    def head(self, url, **kwargs):
        return self.request("head", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("get", url, **kwargs)


class FakeDownloads:
    def __init__(self):
        self.effects = []
        self.options = []
        self.clients = []
        self.calls = []
        self.constructor_error = None
        self.exit_error = None
        self.emit_diagnostics = False
        self.closed = threading.Event()

    def open(self, options):
        self.options.append(options)
        if self.constructor_error:
            raise self.constructor_error
        client = FakeDownloader(self, options)
        self.clients.append(client)
        return client


class FakeDownloader:
    def __init__(self, owner, options):
        self.owner = owner
        self.options = options
        self.closed = False
        self.exit_type = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exit_type = exc_type
        self.closed = True
        self.owner.closed.set()
        if self.owner.exit_error:
            raise self.owner.exit_error

    def extract_info(self, value, *, download):
        self.owner.calls.append((value, download, threading.get_ident()))
        if self.owner.emit_diagnostics:
            for name in ("debug", "warning", "error"):
                getattr(self.options["logger"], name)(SECRET + " " + PROXY)
        if not self.owner.effects:
            raise AssertionError("unexpected fake download")
        effect = self.owner.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect() if callable(effect) else effect


class ThreadGate:
    """Hold fake I/O in its actual worker thread until the test releases it."""

    def __init__(self, result):
        self.result = result
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.thread_id = None

    def __call__(self):
        self.thread_id = threading.get_ident()
        self.started.set()
        self.loop.call_soon_threadsafe(self.entered.set)
        try:
            if not self.release.wait(WATCHDOG * 2):
                raise TimeoutError("test I/O gate watchdog expired")
            return self.result
        finally:
            self.finished.set()


class AsyncPlatformTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.profile = self.temp / "plugin-profile"
        self.http = FakeHTTP()
        self.downloads = FakeDownloads()
        self.logs = Mock()
        self.stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.stack.enter_context(patch.dict(os.environ, {
            "HTTPS_PROXY": "", "HTTP_PROXY": "", "ALL_PROXY": "",
            "https_proxy": "", "http_proxy": "", "all_proxy": "",
            "HOME": str(self.temp / "home"), "USERPROFILE": str(self.temp / "home"),
            "LOCALAPPDATA": str(self.temp / "local"),
            "BILI_USER_DATA_DIR": str(self.temp / "foreign-profile"),
        }))
        self.network_guards = [self.stack.enter_context(patch(
            target, side_effect=AssertionError("real network is forbidden")
        )) for target in ("socket.socket.connect", "socket.socket.connect_ex",
                         "socket.create_connection", "socket.getaddrinfo")]
        modules = {}
        for name, folder in ((PACKAGE, ROOT), (f"{PACKAGE}.core", ROOT / "core"),
                             (f"{PACKAGE}.services", ROOT / "services"),
                             (f"{PACKAGE}.utils", ROOT / "utils")):
            module = ModuleType(name)
            module.__package__ = name
            module.__path__ = [str(folder)]
            modules[name] = module
        config = ModuleType(f"{PACKAGE}.core.config")
        self.config_guard = Mock(side_effect=AssertionError("real configuration access is forbidden"))
        config.__getattr__ = self.config_guard
        modules[config.__name__] = config
        display = ModuleType(f"{PACKAGE}.utils.display")
        display.log = self.logs
        modules[display.__name__] = display
        requests = ModuleType("requests")
        requests.head, requests.get = self.http.head, self.http.get
        modules["requests"] = requests
        downloader = ModuleType("yt_dlp")
        downloader.YoutubeDL = self.downloads.open
        modules["yt_dlp"] = downloader
        transcript = ModuleType("youtube_transcript_api")
        transcript.YouTubeTranscriptApi = Mock(side_effect=AssertionError("transcripts are forbidden"))
        modules["youtube_transcript_api"] = transcript
        self.stack.enter_context(patch.dict(sys.modules, modules))
        # Load only these source modules, never the application's package/config initializers.
        runtime = self.load("_runtime", "_runtime.py")
        runtime.configure_plugin(self.profile)
        self.user_data = self.load("core.user_data", "core/user_data.py")
        self.adapter = self.load("services.platform_adapter", "services/platform_adapter.py")
        self.assertEqual(self.user_data.USER_DATA_DIR, self.profile)
        self.assertFalse(self.profile.exists(), "imports must not create a user profile")

    def tearDown(self):
        self.config_guard.assert_not_called()
        for guard in self.network_guards:
            guard.assert_not_called()
        self.assertFalse((self.temp / "foreign-profile").exists())

    def load(self, name, filename):
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    async def invoke(self, name, *args, asynchronous=True, **kwargs):
        if asynchronous:
            return await getattr(self.adapter, name + "_async")(*args, **kwargs)
        return getattr(self.adapter, name)(*args, **kwargs)

    def assert_requests(self, methods, url=SHORT, timeout=10):
        self.assertEqual([call[0] for call in self.http.calls], methods)
        for _, actual_url, options, _ in self.http.calls:
            self.assertEqual(actual_url, url)
            self.assertEqual(options, {
                "allow_redirects": True, "timeout": timeout,
                "headers": {"User-Agent": self.adapter._UA}, "stream": True,
            })

    def assert_response_closed(self, response, exit_type=None):
        self.assertEqual(response.status_checks, 1)
        self.assertEqual(response.close_calls, 1)
        self.assertTrue(response.closed.is_set())
        self.assertIs(response.exit_type, exit_type)

    def assert_safe_error(self, result, error_type):
        self.assertEqual(result, (None, 0.0, 0.0, {
            "error": f"Video download failed ({error_type})",
        }))
        for private in (SECRET, PROXY, "synthetic-cookie", "synthetic-key", "synthetic-password"):
            self.assertNotIn(private, str(result))
            self.assertNotIn(private, str(self.logs.call_args_list))

    async def assert_heartbeat_during_io(self, task, gate):
        await asyncio.wait_for(gate.entered.wait(), WATCHDOG)
        self.assertNotEqual(gate.thread_id, threading.get_ident(), "fake I/O must run off-loop")

        async def heartbeat():
            await asyncio.sleep(0)
            # Observe inside the I/O window, before releasing it or awaiting its result.
            self.assertTrue(gate.started.is_set())
            self.assertFalse(gate.release.is_set())
            self.assertFalse(gate.finished.is_set())
            self.assertFalse(task.done())
            return "heartbeat advanced while worker I/O was blocked"

        beat = asyncio.create_task(heartbeat())
        self.assertEqual(await asyncio.wait_for(beat, WATCHDOG),
                         "heartbeat advanced while worker I/O was blocked")

    async def drain_gate(self, task, gate, closed):
        gate.release.set()
        if not task.done():
            await asyncio.wait_for(task, WATCHDOG)
        elif not task.cancelled():
            task.result()
        if task.cancelled() and gate.started.is_set():
            # Cancelling the await does not join/kill the worker; drain it explicitly.
            self.assertTrue(await asyncio.wait_for(
                asyncio.to_thread(closed.wait, WATCHDOG), WATCHDOG + 1))

    async def test_short_url_head_success_timeout_options_and_close(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                self.http.calls.clear()
                response = FakeResponse()
                self.http.effects["head"] = [response]
                self.assertEqual(await self.invoke("resolve_bilibili_short_url", SHORT, timeout=7,
                                                   asynchronous=asynchronous), LONG)
                self.assert_requests(["head"], timeout=7)
                self.assert_response_closed(response)

    async def test_short_url_head_timeout_or_http_failure_falls_back_to_get(self):
        for failure in (TimeoutError(SECRET), FakeResponse(status=405), FakeResponse(status=503)):
            with self.subTest(failure=type(failure).__name__, status=getattr(failure, "status_code", None)):
                self.http.calls.clear()
                response = FakeResponse()
                self.http.effects = {"head": [failure], "get": [response]}
                self.assertEqual(await self.adapter.resolve_bilibili_short_url_async(SHORT), LONG)
                self.assert_requests(["head", "get"])
                self.assert_response_closed(response)
                if isinstance(failure, FakeResponse):
                    self.assert_response_closed(failure, FakeHTTPError)
                self.assertEqual(self.logs.call_args_list, [])

    async def test_short_url_all_failures_return_original_and_close_failed_responses(self):
        for head, get in ((TimeoutError(SECRET), TimeoutError(SECRET)),
                          (FakeResponse(status=403), FakeResponse(status=502)),
                          (FakeResponse(status=500), TimeoutError(SECRET))):
            with self.subTest(head=type(head).__name__, get=type(get).__name__):
                self.http.calls.clear()
                self.http.effects = {"head": [head], "get": [get]}
                self.assertEqual(await self.adapter.resolve_bilibili_short_url_async(SHORT, 4), SHORT)
                self.assert_requests(["head", "get"], timeout=4)
                for response in (head, get):
                    if isinstance(response, FakeResponse):
                        self.assert_response_closed(response, FakeHTTPError)
                self.assertEqual(self.logs.call_args_list, [])

    async def test_short_url_empty_redirect_preserves_original_without_get(self):
        response = FakeResponse(url="")
        self.http.effects["head"] = [response]
        self.assertEqual(await self.adapter.resolve_bilibili_short_url_async(SHORT), SHORT)
        self.assert_requests(["head"])
        self.assert_response_closed(response)

    async def test_short_url_missing_requests_and_non_short_values_do_not_use_network(self):
        for value in (None, "", BV, DIRECT, "https://example.invalid/video"):
            self.assertEqual(await self.adapter.resolve_bilibili_short_url_async(value), value)
        with patch.object(self.adapter, "requests", None):
            self.assertEqual(await self.adapter.resolve_bilibili_short_url_async(SHORT), SHORT)
        self.assertEqual(self.http.calls, [])

    async def test_normalize_resolves_only_original_short_url_once(self):
        for asynchronous in (False, True):
            for case in ("redirect", "still-short-with-id", "still-short", "get-fallback", "failed-with-id"):
                with self.subTest(asynchronous=asynchronous, case=case):
                    original = SHORT
                    expected_id, expected_url = BV, DIRECT + "?p=3"
                    response = None
                    if case == "get-fallback":
                        response = FakeResponse()
                        effects = {"head": [TimeoutError(SECRET)], "get": [response]}
                    elif case == "failed-with-id":
                        original += f"/{BV}?p=3"
                        effects = {"head": [TimeoutError(SECRET)], "get": [TimeoutError(SECRET)]}
                    else:
                        destination = {"redirect": LONG, "still-short-with-id": f"{SHORT}/{BV}?p=3",
                                       "still-short": SHORT}[case]
                        response = FakeResponse(destination)
                        effects = {"head": [response], "get": []}
                        if case == "still-short":
                            expected_id, expected_url = "", original
                    self.http.calls.clear()
                    self.http.effects = effects
                    with patch.object(self.adapter, "resolve_bilibili_short_url",
                                      wraps=self.adapter.resolve_bilibili_short_url) as resolve:
                        result = await self.invoke("normalize_video_input", "  " + original + "  ",
                                                   asynchronous=asynchronous)
                        resolve.assert_called_once_with(original)
                    self.assertEqual(result, {"ok": True, "platform": "bilibili",
                                              "video_id": expected_id, "url": expected_url})
                    methods = ["head", "get"] if case in ("get-fallback", "failed-with-id") else ["head"]
                    self.assert_requests(methods, url=original)
                    if response:
                        self.assert_response_closed(response)

    async def test_bv_and_direct_inputs_never_repeat_network(self):
        for asynchronous in (False, True):
            for value, page in ((BV, None), (DIRECT, None), (DIRECT + "?p=2", 2), (DIRECT + "/p4", 4)):
                with self.subTest(asynchronous=asynchronous, value=value):
                    expected = {"ok": True, "platform": "bilibili", "video_id": BV,
                                "url": DIRECT + (f"?p={page}" if page else "")}
                    self.assertEqual(await self.invoke("extract_video_id", value,
                                                       asynchronous=asynchronous), BV)
                    self.assertEqual(await self.invoke("extract_bilibili_p_number", value,
                                                       asynchronous=asynchronous), page)
                    self.assertEqual(await self.invoke("normalize_video_input", value,
                                                       asynchronous=asynchronous), expected)
                    metadata = await self.invoke("fetch_platform_metadata", value, cfg={},
                                                 prefer_subtitle=False, asynchronous=asynchronous)
                    self.assertEqual(metadata, {**expected, "message": "B站视频将使用现有 BiliClient / 字幕强链路"})
        self.assertEqual(self.http.calls, [])

    async def test_normalization_and_metadata_return_contracts(self):
        local = self.temp / "input.mp4"
        local.write_bytes(b"synthetic local input")
        cases = [
            ("", "unknown", ""), ("not-a-video", "unknown", ""),
            ("https://www.youtube.com/watch?v=abcdefghijk", "youtube", "abcdefghijk"),
            ("https://youtu.be/abcdefghijk", "youtube", "abcdefghijk"),
            ("https://www.youtube.com/shorts/abcdefghijk", "youtube", "abcdefghijk"),
            ("https://www.douyin.com/video/123456", "douyin", "123456"),
            ("https://www.kuaishou.com/short-video/synthetic-id", "kuaishou", "synthetic-id"),
            ("https://example.invalid/video", "web", ""), (str(local), "local", ""),
        ]
        for asynchronous in (False, True):
            for value, platform, video_id in cases:
                with self.subTest(asynchronous=asynchronous, platform=platform, value=value):
                    ok = platform != "unknown"
                    expected = {"ok": ok, "platform": platform, "video_id": video_id, "url": value}
                    self.assertEqual(await self.invoke("normalize_video_input", " " + value + " ",
                                                       asynchronous=asynchronous), expected)
                    self.assertEqual(await self.invoke("extract_video_id", value, platform,
                                                       asynchronous=asynchronous), video_id)
                    self.assertEqual(self.adapter.is_supported_video_input(value), ok)
                    metadata = await self.invoke("fetch_platform_metadata", value,
                                                 asynchronous=asynchronous)
                    if ok:
                        self.assertEqual(metadata, {**expected, "supported_analysis": False,
                            "message": f"{self.adapter.PLATFORM_LABELS[platform]} 已识别，但当前阶段暂不支持分析（仅 B站可用）"})
                    else:
                        self.assertEqual(metadata, {**expected,
                            "message": "暂不支持该链接格式（目前仅完整支持 B站）"})
        self.assertEqual(self.http.calls, [])

    async def test_page_number_contract(self):
        for suffix, expected in (("?p=1", 1), ("?p=0", None), ("?p=-1", None),
                                 ("?other=1&p=12", 12), ("/p7", 7), ("/p0", None), ("", None)):
            with self.subTest(suffix=suffix):
                self.assertEqual(await self.adapter.extract_bilibili_p_number_async(DIRECT + suffix), expected)
                normalized = await self.adapter.normalize_video_input_async(DIRECT + suffix)
                self.assertEqual(normalized["url"], DIRECT + (f"?p={expected}" if expected else ""))
        self.assertEqual(self.http.calls, [])

    async def test_http_async_wrappers_advance_heartbeat_inside_thread_io(self):
        norm = {"ok": True, "platform": "bilibili", "video_id": BV, "url": DIRECT + "?p=3"}
        cases = [
            ("resolve_bilibili_short_url", {"timeout": 7}, LONG),
            ("extract_video_id", {"platform": "bilibili"}, BV),
            ("extract_bilibili_p_number", {}, 3),
            ("normalize_video_input", {}, norm),
            ("fetch_platform_metadata", {"cfg": {}, "prefer_subtitle": False},
             {**norm, "message": "B站视频将使用现有 BiliClient / 字幕强链路"}),
        ]
        for name, kwargs, expected in cases:
            with self.subTest(wrapper=name):
                response = FakeResponse()
                gate = ThreadGate(response)
                self.http.calls.clear()
                self.http.effects = {"head": [gate], "get": []}
                task = asyncio.create_task(self.invoke(name, SHORT, **kwargs))
                try:
                    await self.assert_heartbeat_during_io(task, gate)
                    gate.release.set()
                    self.assertEqual(await asyncio.wait_for(task, WATCHDOG), expected)
                finally:
                    await self.drain_gate(task, gate, response.closed)
                self.assert_requests(["head"], timeout=kwargs.get("timeout", 10))
                self.assert_response_closed(response)

    async def test_download_success_uses_resolved_temp_root_and_bounded_options(self):
        cases = [(None, self.profile / "Data" / "downloads"),
                 ({}, self.profile / "Data" / "downloads"),
                 ({"video": {"download_dir": ""}}, self.profile / "Data" / "downloads"),
                 ({"video": {"download_dir": "media/nested"}}, self.profile / "media" / "nested"),
                 ({"video": {"download_dir": self.profile / "absolute"}}, self.profile / "absolute")]
        for asynchronous in (False, True):
            for cfg, directory in cases:
                with self.subTest(asynchronous=asynchronous, cfg=cfg):
                    self.downloads.effects = [INFO.copy()]
                    with patch.object(self.adapter, "resolve_output_path",
                                      wraps=self.adapter.resolve_output_path) as resolve:
                        result = await self.invoke("download_platform_video", "https://video.invalid/id", cfg,
                                                   asynchronous=asynchronous)
                        resolve.assert_called_once_with(((cfg or {}).get("video") or {}).get("download_dir"),
                                                        self.user_data.DATA_DIR / "downloads")
                    self.assertEqual(result, (str(directory / "synthetic-id.m4a"), 12.5, 0.0,
                                              {"video_id": "synthetic-id", "title": "Synthetic title"}))
                    self.assertIs(type(result[1]), float)
                    self.assertIs(type(result[2]), float)
                    self.assertTrue(directory.is_dir())
                    options = self.downloads.options[-1]
                    self.assertEqual(Path(options["outtmpl"]), directory / "%(id)s.%(ext)s")
                    self.assertTrue(math.isfinite(options["socket_timeout"]))
                    self.assertEqual(options["socket_timeout"], 15)
                    for key in ("retries", "fragment_retries", "extractor_retries"):
                        self.assertIs(type(options[key]), int)
                        self.assertEqual(options[key], 2)
                    for key in ("noplaylist", "quiet", "no_warnings"):
                        self.assertIs(options[key], True)
                    self.assertNotIn("proxy", options)
                    self.assertEqual(self.downloads.calls[-1][:2], ("https://video.invalid/id", True))
                    self.assertTrue(self.downloads.clients[-1].closed)
                    self.assertIsNone(self.downloads.clients[-1].exit_type)
        self.assertEqual(self.http.calls, [])

    async def test_download_proxy_precedence_uses_only_synthetic_configuration(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://https.invalid:1", "HTTP_PROXY": "http://http.invalid:2"}):
            for cfg, expected in (({"platform_adapter": {"proxy": PROXY}}, PROXY),
                                  ({}, "http://https.invalid:1")):
                self.downloads.effects = [INFO.copy()]
                await self.adapter.download_platform_video_async("https://video.invalid/id", cfg)
                self.assertEqual(self.downloads.options[-1]["proxy"], expected)
            with patch.dict(os.environ, {"HTTPS_PROXY": ""}):
                self.assertEqual(self.adapter.get_proxy({}), "http://http.invalid:2")
        self.assertEqual(self.adapter.get_proxy({}), "")

    async def test_download_rejects_output_escape_before_downloader_creation(self):
        for value in ("../escape", self.temp / "outside", self.temp / "plugin-profile-other"):
            with self.subTest(value=value):
                result = await self.adapter.download_platform_video_async(
                    "https://video.invalid/id", {"video": {"download_dir": value}})
                self.assert_safe_error(result, "ValueError")
        self.assertEqual(self.downloads.options, [])
        self.assertFalse(self.profile.exists())
        self.assertFalse((self.temp / "escape").exists())
        self.assertFalse((self.temp / "outside").exists())
        self.assertFalse((self.temp / "plugin-profile-other").exists())

    async def test_download_missing_dependency_keeps_error_tuple_without_output(self):
        with patch.object(self.adapter, "yt_dlp", None):
            for asynchronous in (False, True):
                self.assertEqual(await self.invoke("download_platform_video", SHORT,
                                                   asynchronous=asynchronous),
                                 (None, 0.0, 0.0, {"error": "yt_dlp 未安装"}))
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.http.calls, [])

    async def test_download_default_duration_and_extension_contract(self):
        self.downloads.effects = [{"id": "synthetic-id", "title": "Synthetic title"}]
        self.assertEqual(await self.adapter.download_platform_video_async("https://video.invalid/id"),
                         (str(self.profile / "Data" / "downloads" / "synthetic-id.m4a"),
                          0.0, 0.0, {"video_id": "synthetic-id", "title": "Synthetic title"}))
        self.assertTrue(self.downloads.clients[-1].closed)

    async def test_download_proxy_configuration_failures_keep_safe_tuple(self):
        class BrokenProxy(dict):
            def get(self, *args):
                raise RuntimeError(SECRET + " " + PROXY)

        for asynchronous in (False, True):
            for proxy, error_type in (("malformed-nested-config", "AttributeError"),
                                      (BrokenProxy(proxy=PROXY), "RuntimeError")):
                with self.subTest(asynchronous=asynchronous, error_type=error_type):
                    result = await self.invoke("download_platform_video", SHORT,
                                               {"platform_adapter": proxy}, asynchronous=asynchronous)
                    self.assert_safe_error(result, error_type)
        self.assertEqual(self.downloads.options, [])
        self.assertFalse(self.profile.exists())

    async def test_download_mkdir_failure_is_sanitized(self):
        with patch.object(Path, "mkdir", side_effect=PermissionError(SECRET + " " + PROXY)):
            result = await self.adapter.download_platform_video_async("https://video.invalid/id")
        self.assert_safe_error(result, "PermissionError")
        self.assertEqual(self.downloads.options, [])

    async def test_download_constructor_extract_conversion_and_exit_failures_are_safe(self):
        for stage, error_type in (("constructor", "RuntimeError"), ("extract", "RuntimeError"),
                                  ("duration", "ValueError"), ("exit", "RuntimeError")):
            with self.subTest(stage=stage):
                self.downloads.constructor_error = None
                self.downloads.exit_error = None
                self.downloads.effects = [INFO.copy()]
                self.downloads.emit_diagnostics = True
                self.logs.reset_mock()
                if stage == "constructor":
                    self.downloads.constructor_error = RuntimeError(SECRET + " " + PROXY)
                elif stage == "extract":
                    self.downloads.effects = [RuntimeError(SECRET + " " + PROXY)]
                elif stage == "duration":
                    self.downloads.effects = [{**INFO, "duration": SECRET}]
                else:
                    self.downloads.exit_error = RuntimeError(SECRET + " " + PROXY)
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    result = await self.adapter.download_platform_video_async(
                        "https://video.invalid/id", {"platform_adapter": {"proxy": PROXY}})
                self.assert_safe_error(result, error_type)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), "")
                if stage != "constructor":
                    self.assertTrue(self.downloads.clients[-1].closed)
                    self.assertEqual(self.logs.call_args_list, [
                        unittest.mock.call("视频下载器报告警告", "WARN"),
                        unittest.mock.call("视频下载器报告错误", "ERROR"),
                    ])

    async def test_download_async_wrapper_advances_heartbeat_inside_thread_io(self):
        gate = ThreadGate(INFO.copy())
        self.downloads.effects = [gate]
        cfg = {"video": {"download_dir": "gated/audio"}, "platform_adapter": {"proxy": PROXY}}
        task = asyncio.create_task(self.adapter.download_platform_video_async("https://video.invalid/id", cfg))
        try:
            await self.assert_heartbeat_during_io(task, gate)
            self.assertFalse(self.downloads.clients[-1].closed)
            gate.release.set()
            self.assertEqual(await asyncio.wait_for(task, WATCHDOG),
                             (str(self.profile / "gated" / "audio" / "synthetic-id.m4a"),
                              12.5, 0.0, {"video_id": "synthetic-id", "title": "Synthetic title"}))
        finally:
            await self.drain_gate(task, gate, self.downloads.closed)
        self.assertEqual(self.downloads.options[-1]["proxy"], PROXY)
        self.assertTrue(self.downloads.clients[-1].closed)

    async def test_cancel_short_url_await_leaves_worker_running_until_gate_release(self):
        response = FakeResponse()
        gate = ThreadGate(response)
        self.http.effects["head"] = [gate]
        task = asyncio.create_task(self.adapter.resolve_bilibili_short_url_async(SHORT))
        try:
            await self.assert_heartbeat_during_io(task, gate)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertFalse(gate.release.is_set())
            self.assertFalse(gate.finished.is_set(), "cancellation must not be mistaken for thread termination")
            self.assertFalse(response.closed.is_set())
        finally:
            await self.drain_gate(task, gate, response.closed)
        self.assertTrue(gate.finished.is_set())
        self.assert_response_closed(response)
        self.assert_requests(["head"])

    async def test_cancel_download_await_leaves_worker_running_until_gate_release(self):
        gate = ThreadGate(INFO.copy())
        self.downloads.effects = [gate]
        task = asyncio.create_task(self.adapter.download_platform_video_async("https://video.invalid/id"))
        try:
            await self.assert_heartbeat_during_io(task, gate)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(task.cancelled())
            self.assertFalse(gate.release.is_set())
            self.assertFalse(gate.finished.is_set(), "cancellation must not be mistaken for thread termination")
            self.assertFalse(self.downloads.clients[-1].closed)
        finally:
            await self.drain_gate(task, gate, self.downloads.closed)
        self.assertTrue(gate.finished.is_set())
        self.assertTrue(self.downloads.clients[-1].closed)
        self.assertIsNone(self.downloads.clients[-1].exit_type)


if __name__ == "__main__":
    unittest.main()
