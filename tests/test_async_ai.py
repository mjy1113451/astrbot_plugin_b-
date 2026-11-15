"""Async AI regressions: isolated fake SDK/HTTP transports, no network or user data.

Run: python -B -m unittest discover -s tests -p test_async_ai.py -v
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_async_ai_test_plugin"
SECRET = "synthetic-response-body tool-args tool-result cookie=synthetic key=synthetic"
TOOLS = [{"type": "function", "function": {"name": "lookup"}}]


def response(content="answer", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    message.model_dump = lambda: {
        "role": "assistant", "content": content,
        "tool_calls": [{"id": tc.id, "type": "function", "function": {
            "name": tc.function.name, "arguments": tc.function.arguments,
        }} for tc in (tool_calls or [])],
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def tool_response():
    return response(None, [SimpleNamespace(id="call-1", function=SimpleNamespace(
        name="lookup", arguments=json.dumps({"query": SECRET})))])


class FakeStatusError(Exception):
    def __init__(self, message, *, request=None, response=None, status=None):
        super().__init__(message)
        self.request = request
        self.response = response
        self.status_code = status if status is not None else getattr(response, "status_code", None)


class FakeSDK:
    def __init__(self):
        self.clients = []
        self.transports = []
        self.calls = []
        self.posts = []
        self.effects = []
        self.http_effects = []
        self.delay = 0
        self.started = asyncio.Event()
        self.release = None
        self.constructor_error = None
        sdk = self

        class AsyncClient:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.closed = False
                self.exit_type = None
                sdk.transports.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                self.exit_type = exc_type
                await self.aclose()

            async def aclose(self):
                self.closed = True

            async def post(self, url, **kwargs):
                sdk.posts.append((url, kwargs))
                if sdk.http_effects:
                    effect = sdk.http_effects.pop(0)
                    if isinstance(effect, BaseException):
                        raise effect
                    return effect
                return SimpleNamespace(
                    status_code=200, raise_for_status=lambda: None,
                    json=lambda: {"choices": [{"message": {"content": "http answer"}}]},
                )

        class AsyncOpenAI:
            def __init__(self, **kwargs):
                if sdk.constructor_error:
                    raise sdk.constructor_error
                self.options = kwargs
                self.closed = False
                self.exit_type = None
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
                sdk.clients.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                self.exit_type = exc_type
                await self.close()

            async def close(self):
                self.closed = True
                if self.options.get("http_client"):
                    await self.options["http_client"].aclose()

            async def create(self, **kwargs):
                sdk.calls.append(kwargs)
                sdk.started.set()
                if sdk.release:
                    await sdk.release.wait()
                if sdk.delay:
                    await asyncio.sleep(sdk.delay)
                effect = sdk.effects.pop(0) if sdk.effects else response()
                if isinstance(effect, BaseException):
                    raise effect
                return effect

        self.AsyncClient = AsyncClient
        self.AsyncOpenAI = AsyncOpenAI


class AsyncAITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sdk = FakeSDK()
        self.logs = Mock()
        self.config = {"api": {
            "unified_api_key": "synthetic-api-key", "unified_base_url": "https://ai.invalid/v1",
            "model_brain": "chat-before", "model_vision": "vision-before", "max_retries": 3,
        }}
        self.proxy_url = "http://proxy.invalid:8080"
        modules = {}
        for name, folder in ((PACKAGE, ROOT), (f"{PACKAGE}.brain", ROOT / "brain"),
                             (f"{PACKAGE}.services", ROOT / "services"),
                             (f"{PACKAGE}.utils", ROOT / "utils"),
                             (f"{PACKAGE}.core", ROOT / "core")):
            module = ModuleType(name)
            module.__path__ = [str(folder)]
            module.__package__ = name
            modules[name] = module
        config = ModuleType(f"{PACKAGE}.core.config")
        config.config = self.config
        config.load_config = Mock(side_effect=lambda: self.config)
        modules[config.__name__] = config
        display = ModuleType(f"{PACKAGE}.utils.display")
        display.log = self.logs
        modules[display.__name__] = display
        proxy = ModuleType(f"{PACKAGE}.services.proxy_config")
        proxy.get_proxy_url = lambda: self.proxy_url
        modules[proxy.__name__] = proxy
        helpers = ModuleType(f"{PACKAGE}.utils.helpers")
        helpers._mask_urls = lambda text: text
        modules[helpers.__name__] = helpers
        httpx = ModuleType("httpx")
        httpx.AsyncClient = self.sdk.AsyncClient
        httpx.Client = Mock(side_effect=AssertionError("synchronous HTTP is forbidden"))
        httpx.HTTPStatusError = FakeStatusError
        modules["httpx"] = httpx
        openai = ModuleType("openai")
        openai.AsyncOpenAI = self.sdk.AsyncOpenAI
        openai.OpenAI = Mock(side_effect=AssertionError("synchronous SDK is forbidden"))
        modules["openai"] = openai
        imports = ModuleType(f"{PACKAGE}.brain._mixin_imports")
        for name, value in {"asyncio": asyncio, "json": json, "os": os, "time": time,
                            "httpx": httpx, "AsyncOpenAI": self.sdk.AsyncOpenAI,
                            "OpenAI": openai.OpenAI, "config": self.config,
                            "log": self.logs}.items():
            setattr(imports, name, value)
        modules[imports.__name__] = imports
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.service = self.load("services._services_ai", "services/_services_ai.py")
        self.brain_module = self.load("brain._brain_ai", "brain/_brain_ai.py")
        self.brain = self.brain_module.BrainAIMixin()
        self.brain._preferred_ai_method = None
        self.brain._ai_errors_consecutive = 0
        self.brain._ai_primary_failing = 0
        self.brain._ai_degraded_until = 0
        self.brain._ai_degraded_logged = False
        self.brain._ai_using_fallback_provider = False
        self.brain._ai_fallback_recheck_at = 0

    def load(self, name, filename):
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def assert_closed(self):
        self.assertTrue(self.sdk.clients)
        self.assertTrue(all(client.closed for client in self.sdk.clients))
        self.assertTrue(all(client.closed for client in self.sdk.transports))

    async def invoke(self, kind):
        if kind == "brain":
            return await self.brain._call_ai_via_openai(messages=[], model="chat", request_timeout=9)
        if kind == "service":
            return await self.service._call_ai_via_openai([], timeout=9)
        return await self.service.call_ai_with_tools([], TOOLS, tool_handler=lambda *args: "ok", timeout=9)

    async def test_ordinary_success_object_and_string_contracts(self):
        expected = response("brain answer")
        self.sdk.effects = [expected]
        actual = await self.brain._call_ai_via_openai(
            model="chat", messages=[], request_timeout=7, timeout=2,
            _override_api_key="override-key", _override_base_url="https://override.invalid/v1",
        )
        self.assertIs(actual, expected)
        self.assertEqual(self.sdk.clients[-1].options["timeout"], 7)
        self.assertEqual(self.sdk.clients[-1].options["api_key"], "override-key")
        self.assertEqual(self.sdk.clients[-1].options["base_url"], "https://override.invalid/v1")
        self.assertFalse(any(k.startswith("_") or "timeout" in k for k in self.sdk.calls[-1]))
        self.assertEqual(self.sdk.transports[-1].options, {"proxy": self.proxy_url})
        expected = response("raw answer")
        self.sdk.effects = [expected]
        self.assertIs(await self.service.call_ai_raw([], tools=TOOLS), expected)
        self.assertEqual(self.sdk.calls[-1]["tools"], TOOLS)
        self.assertEqual(await self.service.call_ai([]), "answer")
        self.assertTrue(all(client.options["max_retries"] == 0 for client in self.sdk.clients))
        self.assertIs(self.service.log, sys.modules[f"{PACKAGE}.utils.display"].log)
        self.assert_closed()

    async def test_no_proxy_and_live_config_between_calls(self):
        self.proxy_url = ""
        await self.service._call_ai_via_openai([])
        self.config["api"].update(unified_api_key="updated-key", unified_base_url="https://new.invalid/v1",
                                  model_brain="chat-after")
        await self.service._call_ai_via_openai([])
        self.assertEqual(self.sdk.clients[-1].options["api_key"], "updated-key")
        self.assertEqual(self.sdk.clients[-1].options["base_url"], "https://new.invalid/v1")
        self.assertEqual(self.sdk.calls[-1]["model"], "chat-after")
        await self.brain._call_ai_via_openai(messages=[], timeout=11)
        self.assertEqual(self.sdk.clients[-1].options["timeout"], 11)
        self.assertEqual(self.sdk.clients[-1].options["api_key"], "updated-key")
        self.assertEqual(self.sdk.transports, [])
        self.assertTrue(all(client.options["http_client"] is None for client in self.sdk.clients))
        self.assert_closed()

    async def test_sdk_error_closes_clients(self):
        for kind in ("brain", "service"):
            with self.subTest(kind=kind):
                self.sdk.effects = [RuntimeError(SECRET)]
                with self.assertRaises(RuntimeError) as caught:
                    await self.invoke(kind)
                self.assertEqual(str(caught.exception), "RuntimeError")
                self.assertNotIn(SECRET, str(caught.exception))
                self.assert_closed()

    async def test_constructor_failure_closes_proxy(self):
        self.sdk.constructor_error = RuntimeError("construction failed")
        for kind in ("brain", "service", "tools"):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                await self.invoke(kind)
        self.assertEqual(len(self.sdk.transports), 3)
        self.assertTrue(all(client.closed for client in self.sdk.transports))

    async def test_cancel_during_sdk_wait_closes_and_propagates(self):
        for kind in ("brain", "service", "tools"):
            with self.subTest(kind=kind):
                self.sdk.started.clear()
                self.sdk.release = asyncio.Event()
                task = asyncio.create_task(self.invoke(kind))
                await asyncio.wait_for(self.sdk.started.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIs(self.sdk.clients[-1].exit_type, asyncio.CancelledError)
                self.assert_closed()

    async def test_tools_rounds_and_final_are_awaited_with_default_sdk_retries(self):
        seen = []

        async def handler(name, args):
            await asyncio.sleep(0)
            seen.append((name, args))
            return {"result": SECRET}

        self.sdk.effects = [tool_response(), tool_response(), response("final answer")]
        messages = []
        result = await self.service.call_ai_with_tools(
            messages, TOOLS, max_tool_rounds=2, tool_handler=handler,
        )
        self.assertEqual(result, "final answer")
        self.assertEqual(seen, [("lookup", {"query": SECRET})] * 2)
        self.assertEqual(len(self.sdk.calls), 3)
        self.assertTrue(all(call["tools"] == TOOLS for call in self.sdk.calls[:2]))
        self.assertNotIn("tools", self.sdk.calls[-1])
        self.assertNotIn("max_retries", self.sdk.clients[-1].options)
        self.assertEqual(len(self.sdk.clients), 1)
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(json.loads(messages[-1]["content"]), {"result": SECRET})
        self.assertNotIn(SECRET, str(self.logs.call_args_list))
        self.assert_closed()

    async def test_tools_text_call_keeps_sync_handler_protocol_and_safe_logs(self):
        handler = Mock(return_value=SECRET)
        self.sdk.effects = [response(json.dumps({"name": "lookup", "arguments": {"query": SECRET}})),
                            response("done")]
        messages = []
        self.assertEqual(await self.service.call_ai_with_tools(
            messages, TOOLS, max_tool_rounds=1, tool_handler=handler), "done")
        handler.assert_called_once_with("lookup", {"query": SECRET})
        self.assertIn(SECRET, messages[-1]["content"])
        self.assertNotIn(SECRET, str(self.logs.call_args_list))
        self.assert_closed()

    async def test_tools_early_return_and_failure_cleanup(self):
        self.assertEqual(await self.invoke("tools"), "answer")
        self.sdk.effects = [FakeStatusError(SECRET, status=503), FakeStatusError(SECRET, status=502)]
        self.assertEqual(await self.invoke("tools"), "")
        self.assertIn("HTTP 503", str(self.logs.call_args_list))
        self.assertIn("HTTP 502", str(self.logs.call_args_list))
        self.assertNotIn(SECRET, str(self.logs.call_args_list))
        self.assert_closed()

    async def test_handler_exception_is_not_sent_to_model(self):
        def handler(name, args):
            raise ValueError(SECRET)

        self.sdk.effects = [tool_response(), response("done")]
        messages = []
        await self.service.call_ai_with_tools(messages, TOOLS, max_tool_rounds=1, tool_handler=handler)
        self.assertEqual(messages[-1]["content"], "工具执行错误: ValueError")
        self.assertNotIn(SECRET, str(self.logs.call_args_list))
        self.assert_closed()

    async def test_cancel_during_tool_handler_closes_and_propagates(self):
        started = asyncio.Event()

        async def handler(name, args):
            started.set()
            await asyncio.Event().wait()

        self.sdk.effects = [tool_response()]
        task = asyncio.create_task(self.service.call_ai_with_tools([], TOOLS, tool_handler=handler))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_closed()

    async def test_tools_final_cancel_cleanup(self):
        self.sdk.release = asyncio.Event()
        task = asyncio.create_task(self.service.call_ai_with_tools(
            [], TOOLS, max_tool_rounds=0, tool_handler=lambda *args: "ok"))
        await asyncio.wait_for(self.sdk.started.wait(), 1)
        self.assertNotIn("tools", self.sdk.calls[-1])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_closed()

    async def test_heartbeat_runs_inside_request_wait_window(self):
        for kind in ("brain", "service", "tools"):
            with self.subTest(kind=kind):
                self.sdk.started.clear()
                self.sdk.release = asyncio.Event()
                task = asyncio.create_task(self.invoke(kind))
                await asyncio.wait_for(self.sdk.started.wait(), 1)
                # Gate the response on heartbeat ticks, not Windows timer precision.
                ticks = 0
                for _ in range(3):
                    await asyncio.sleep(0.005)
                    self.assertFalse(task.done(), "heartbeat must run while the SDK is still waiting")
                    ticks += 1
                self.assertEqual(ticks, 3)
                self.sdk.release.set()
                await asyncio.wait_for(task, 1)
                self.assert_closed()

    async def test_http_fallback_timeout_alias_and_safe_failure(self):
        await self.brain._call_ai_via_httpx(messages=[], model="chat", timeout=13)
        self.assertEqual(self.sdk.transports[-1].options["timeout"], 13)
        self.assertEqual(json.loads(self.sdk.posts[-1][1]["content"])["model"], "chat")
        await self.brain._call_ai_via_httpx(messages=[], request_timeout=7, timeout=13)
        self.assertEqual(self.sdk.transports[-1].options["timeout"], 7)
        bad = SimpleNamespace(status_code=400, text=SECRET, request=object())

        def fail():
            raise FakeStatusError(SECRET, response=bad, request=bad.request)

        bad.raise_for_status = fail
        for call in (lambda: self.brain._call_ai_via_httpx(messages=[]),
                     lambda: self.service._call_ai_via_httpx([])):
            self.sdk.http_effects = [bad]
            with self.assertRaises(FakeStatusError) as caught:
                await call()
            self.assertEqual(str(caught.exception), "HTTP 400")
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertTrue(self.sdk.transports[-1].closed)

    async def test_service_retry_classification_and_safe_error_logs(self):
        self.sdk.effects = [FakeStatusError(SECRET, status=503)]
        self.sdk.http_effects = [FakeStatusError(SECRET, status=503)]
        waits = []
        real_sleep = asyncio.sleep

        async def sleep(delay):
            waits.append(delay)
            await real_sleep(0)

        with patch.object(self.service.asyncio, "sleep", side_effect=sleep):
            self.assertEqual(await self.service.call_ai([]), "answer")
        self.assertEqual(waits, [2.0])
        self.assertNotIn(SECRET, str(self.logs.call_args_list))
        self.assertIn("HTTP 503", str(self.logs.call_args_list))
        for status in (402, 429):
            self.sdk.effects = [FakeStatusError(SECRET, status=status)]
            with self.assertRaises(RuntimeError) as caught:
                await self.service.call_ai_raw([])
            self.assertIn(f"HTTP {status}", str(caught.exception))
            self.assertNotIn(SECRET, str(caught.exception))
        self.assertGreater(self.service._ai_429_cooldown_until, time.time())
        calls_before = len(self.sdk.calls)
        self.assertEqual(await self.service.call_ai([]), "")
        self.assertEqual(len(self.sdk.calls), calls_before)
        self.assert_closed()

    async def test_brain_body_classification_remains_private(self):
        self.brain._preferred_ai_method = "httpx"
        self.brain._get_ai_backends = lambda: ["httpx"]
        self.config["fallback_models"] = {"chat": "fallback-chat"}
        bad = SimpleNamespace(status_code=404, text="model_not_found " + SECRET, request=object())

        def fail():
            raise FakeStatusError(SECRET, response=bad, request=bad.request)

        bad.raise_for_status = fail
        self.sdk.http_effects = [bad]
        result = await self.brain._call_ai_with_retry(messages=[], model="chat-before")
        self.assertEqual(result.choices[0].message.content, "http answer")
        self.assertEqual(len(self.sdk.posts), 2)
        self.assertEqual(json.loads(self.sdk.posts[-1][1]["content"])["model"], "fallback-chat")
        self.assertNotIn(SECRET, str(self.logs.call_args_list))


if __name__ == "__main__":
    unittest.main()
