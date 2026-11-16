"""Execute real nested subtitle checks and batches with fake HTTP only."""
import ast
import asyncio
import gc
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import warnings


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (("brain/video_analysis.py", "manual_video_analysis"),
           ("cli/app.py", "video_to_html_bg"))
BV = "BV1Q541167Qg"
WBI_URL = "https://api.bilibili.com/x/player/wbi/v2"
FALLBACK_URL = "https://api.bilibili.com/x/player/v2"
SUBTITLE_CASES = (
    ({"data": {"subtitle": {"subtitles": [{"lan": "zh"}]}}}, True),
    ({"data": {"subtitles": [{"lan": "en"}]}}, True),
    ({"data": {"subtitle": {"subtitles": [], "allow_submit": False}}}, False),
    ({"data": {}}, False),
)


def response(status=200, body=None, error=None):
    return SimpleNamespace(status_code=status, json=Mock(return_value=body, side_effect=error))


class FakeClient:
    def __init__(self, responses):
        self.responses = {bvid: list(effects) for bvid, effects in responses.items()}
        self.get = AsyncMock(side_effect=self._get)
        self.closed = False

    def _get(self, url, **kwargs):
        effect = self.responses[kwargs["params"]["bvid"]].pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


def extract_checks(relative, outer_name, client, bvids=()):
    """Compile the production _chk and its enclosing batch, without business imports."""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    outer = next(node for node in tree.body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == outer_name)
    block = next(node for node in ast.walk(outer) if isinstance(node, ast.AsyncWith)
                 and any(isinstance(child, ast.AsyncFunctionDef) and child.name == "_chk"
                         for child in node.body))
    check = next(node for node in block.body
                 if isinstance(node, ast.AsyncFunctionDef) and node.name == "_chk")
    batch = ast.AsyncFunctionDef(
        name="check_batch", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                              kw_defaults=[], defaults=[]),
        body=[block, ast.Return(value=ast.Name(id="sub_status", ctx=ast.Load()))],
        decorator_list=[],
    )
    namespace = {
        "asyncio": asyncio, "httpx": SimpleNamespace(AsyncClient=Mock(return_value=client)),
        "_sc": client, "_wbi_sign": lambda params: params,
        "cookie_dict": {}, "_ua": "subtitle-detection-test",
        "results": [{"bvid": bvid} for bvid in bvids], "sub_status": {},
    }
    module = ast.fix_missing_locations(ast.Module(body=[check, batch], type_ignores=[]))
    exec(compile(module, str(ROOT / relative), "exec"), namespace)
    return namespace["_chk"], namespace["check_batch"]


class SubtitleDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def run_checked(self, operation):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            result = await operation()
            if inspect.isawaitable(result):
                # Never await or close a leaked return value to hide the production bug.
                result = None
            await asyncio.sleep(0)
            gc.collect()
        self.assertEqual([str(item.message) for item in caught
                          if issubclass(item.category, RuntimeWarning)], [])
        return result

    async def check_response_cases(self, fallback):
        for relative, outer_name in SOURCES:
            for body, expected in SUBTITLE_CASES:
                with self.subTest(source=relative, fallback=fallback, body=body):
                    final = response(body=body)
                    effects = [response(412), final] if fallback else [final]
                    client = FakeClient({BV: effects})
                    check, _ = extract_checks(relative, outer_name, client)
                    result = await self.run_checked(lambda: check(BV))
                    self.assertIs(type(result), tuple)
                    self.assertEqual(result, (BV, expected))
                    self.assertIs(type(result[1]), bool)
                    self.assertEqual([call.args[0] for call in client.get.await_args_list],
                                     [WBI_URL, FALLBACK_URL] if fallback else [WBI_URL])
                    final.json.assert_called_once_with()
                    self.assertEqual(client.responses[BV], [])
                    if fallback:
                        effects[0].json.assert_not_called()

    async def test_direct_200_returns_real_tuple(self):
        await self.check_response_cases(fallback=False)

    async def test_412_then_200_returns_real_tuple(self):
        await self.check_response_cases(fallback=True)

    async def test_failures_return_false_tuple(self):
        for relative, outer_name in SOURCES:
            for stage in ("status", "fallback_status", "request", "fallback_request",
                          "json", "fallback_json"):
                with self.subTest(source=relative, stage=stage):
                    if stage.endswith("status"):
                        final = response(503)
                    elif stage.endswith("request"):
                        final = TimeoutError("synthetic request failure")
                    else:
                        final = response(error=ValueError("synthetic malformed JSON"))
                    fallback = stage.startswith("fallback_")
                    client = FakeClient({BV: [response(412), final] if fallback else [final]})
                    check, _ = extract_checks(relative, outer_name, client)
                    self.assertEqual(await self.run_checked(lambda: check(BV)), (BV, False))
                    self.assertEqual(client.get.await_count, 2 if fallback else 1)
                    self.assertEqual(client.responses[BV], [])

    async def test_production_batch_records_subtitle_and_failure_results(self):
        for relative, outer_name in SOURCES:
            for fallback in (False, True):
                with self.subTest(source=relative, fallback=fallback):
                    effects = {}
                    for bvid, body in (("with-subtitle", SUBTITLE_CASES[0][0]),
                                       ("without-subtitle", SUBTITLE_CASES[-1][0])):
                        final = response(body=body)
                        effects[bvid] = [response(412), final] if fallback else [final]
                    effects["request-failure"] = [TimeoutError("synthetic request failure")]
                    client = FakeClient(effects)
                    _, batch = extract_checks(relative, outer_name, client, effects)
                    result = await self.run_checked(batch)
                    self.assertEqual(result, {"with-subtitle": True, "without-subtitle": False,
                                              "request-failure": False})
                    self.assertEqual(client.get.await_count, 5 if fallback else 3)
                    self.assertTrue(client.closed)
                    self.assertTrue(all(not pending for pending in client.responses.values()))


if __name__ == "__main__":
    unittest.main()
