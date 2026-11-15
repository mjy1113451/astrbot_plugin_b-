"""Issue #41 HTML export boundaries using temporary data and fake HTTP."""
from contextlib import ExitStack, redirect_stdout
from importlib import import_module
from io import StringIO
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_ppt_runtime_fixture"
CANARY = "PRIVATE_RESPONSE_CANARY_41"
SLIDES = '<div class="ppt-container">' + '<div class="slide">safe</div>' * 4 + '</div>'


class PptRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.dict(sys.modules))
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        sys.modules[PACKAGE] = package
        self.runtime = import_module(PACKAGE + "._runtime")
        self.runtime.configure_plugin(self.temp / "profile")
        self.data = import_module(PACKAGE + ".core.user_data")
        display = ModuleType(PACKAGE + ".utils.display")
        display.log, display.log_exception = Mock(), Mock()
        sys.modules[display.__name__] = display
        self.display = display
        subtitles = ModuleType(PACKAGE + ".api.subtitles")
        subtitles.fetch_bilibili_subtitles = AsyncMock(return_value=(True, "safe subtitle", "", None))
        sys.modules[subtitles.__name__] = subtitles
        self.responses = []
        self.clients = []
        owner = self

        class RequestError(Exception):
            pass

        class Client:
            def __init__(self, **options):
                self.closed = False
                owner.clients.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get(self, url, **kwargs):
                if url.endswith("/nav"):
                    body = {"code": -1}
                else:
                    body = {"code": 0, "data": {"title": "safe-title", "owner": {}, "stat": {}}}
                return SimpleNamespace(json=lambda: body)

            async def post(self, *args, **kwargs):
                value = owner.responses.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return value

        httpx = ModuleType("httpx")
        httpx.AsyncClient, httpx.RequestError = Client, RequestError
        sys.modules["httpx"] = httpx
        self.httpx = httpx
        self.module = import_module(PACKAGE + ".services.video_to_ppt")
        self.browser = self.stack.enter_context(patch.object(self.module.webbrowser, "open"))
        self.heartbeat = self.stack.enter_context(patch.object(self.module, "_heartbeat_dots", new=AsyncMock()))

    def response(self, *, status=200, content=SLIDES):
        return SimpleNamespace(status_code=status, text=CANARY,
                               json=lambda: {"choices": [{"message": {"content": content}}]})

    async def generate(self, **kwargs):
        return await self.module.generate_ppt_from_bvid(
            "BV1xx411c7mD", "fake-key", "https://fake.invalid/v1", "fake-model", slide_count=4, **kwargs)

    async def test_plugin_export_uses_profile_without_browser_animation_or_stdout(self):
        self.responses.append(self.response())
        output = StringIO()
        with redirect_stdout(output):
            result = await self.generate(output_dir="exports/nested")
        self.assertTrue(result["success"])
        saved = Path(result["html_path"])
        self.assertEqual(saved.parent, self.data.USER_DATA_DIR / "exports" / "nested")
        self.assertIn("safe", saved.read_text(encoding="utf-8"))
        self.assertEqual(output.getvalue(), "")
        self.browser.assert_not_called()
        self.heartbeat.assert_not_called()
        self.assertTrue(all(client.closed for client in self.clients))
        self.display.log.assert_called()

    async def test_http_error_never_returns_response_body_and_closes(self):
        self.responses.append(self.response(status=401))
        result = await self.generate()
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "API错误 401")
        self.assertNotIn(CANARY, str(result))
        self.assertTrue(all(client.closed for client in self.clients))

    async def test_request_failure_uses_safe_error_and_saved_stack(self):
        self.responses.append(self.httpx.RequestError(CANARY))
        result = await self.generate()
        self.assertFalse(result["success"])
        self.assertIn("RequestError", result["error"])
        self.assertNotIn(CANARY, result["error"])
        self.display.log_exception.assert_called_once()
        self.assertTrue(all(client.closed for client in self.clients))

    async def test_repair_failure_never_returns_exception_text(self):
        self.responses.extend([self.response(content='<div class="slide">one</div>'),
                               self.httpx.RequestError(CANARY)])
        result = await self.generate()
        self.assertFalse(result["success"])
        self.assertIn("页数修复失败", result["error"])
        self.assertNotIn(CANARY, result["error"])
        self.assertTrue(all(client.closed for client in self.clients))

    async def test_cancellation_closes_async_client_without_animation(self):
        import asyncio

        entered, release = asyncio.Event(), asyncio.Event()
        client_type = self.httpx.AsyncClient
        original_post = client_type.post

        async def wait_for_cancel(client, *args, **kwargs):
            entered.set()
            await release.wait()
            return await original_post(client, *args, **kwargs)

        with patch.object(client_type, "post", new=wait_for_cancel):
            task = asyncio.create_task(self.generate())
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(all(client.closed for client in self.clients))
        self.heartbeat.assert_not_called()
        self.browser.assert_not_called()

    async def test_generation_rejects_external_output_before_writing(self):
        self.responses.append(self.response())
        outside = self.temp / "outside"
        with self.assertRaises(ValueError):
            await self.generate(output_dir=str(outside))
        self.assertFalse(outside.exists())

    def test_save_retains_nested_paths_and_rejects_escape(self):
        path = Path(self.module.save_html_to_path("safe", "nested/page.html"))
        self.assertEqual(path, self.data.USER_DATA_DIR / "nested" / "page.html")
        self.assertEqual(path.read_text(encoding="utf-8"), "safe")
        with self.assertRaises(ValueError):
            self.module.save_html_to_path("unsafe", "../outside/page.html")
        self.assertFalse((self.temp / "outside").exists())

    def test_save_rejects_final_symlink(self):
        outside = self.temp / "outside.html"
        outside.write_text("untouched", encoding="utf-8")
        self.data.USER_DATA_DIR.mkdir()
        link = self.data.USER_DATA_DIR / "linked.html"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")
        with self.assertRaises(ValueError):
            self.module.save_html_to_path("unsafe", str(link))
        self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

    def test_preview_is_rejected_before_imports_threads_or_socket_activity(self):
        with patch.object(self.module, "_find_free_port", side_effect=AssertionError("socket probed")), \
                patch.object(self.module.threading, "Thread", side_effect=AssertionError("server thread started")):
            with self.assertRaisesRegex(RuntimeError, "standalone"):
                self.module.start_preview_server("safe")
        self.browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
