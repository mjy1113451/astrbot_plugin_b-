"""Regression coverage for live model and video-frame configuration."""
import ast
import asyncio
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_live_brain_config_test_plugin"
MIXINS = {
    "_brain_interact.py": 3,
    "_brain_curiosity.py": 1,
    "_brain_session.py": 2,
    "_brain_loop.py": 1,
    "_brain_learn.py": 3,
    "_brain_video.py": 8,
}


def tree(name):
    return ast.parse((ROOT / "brain" / name).read_text(encoding="utf-8"))


def extract_function(name, function, namespace):
    namespace = {"__package__": f"{PACKAGE}.brain", **namespace}
    node = next(n for n in ast.walk(tree(name))
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, name, "exec"), namespace)
    return namespace[function]


def model_exprs(name):
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "model":
                    yield kw.value


def eval_expr(node, namespace):
    return eval(compile(ast.Expression(node), "<model expression>", "eval"), namespace)


class LiveConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"api": {"model_brain": "brain-before", "model_vision": "vision-before"}}
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT)]
        package.__package__ = PACKAGE
        core = ModuleType(f"{PACKAGE}.core")
        core.__path__ = [str(ROOT / "core")]
        config_module = ModuleType(f"{PACKAGE}.core.config")
        config_module.config = self.cfg
        package.core = core
        core.config = config_module
        self.modules = patch.dict(sys.modules, {PACKAGE: package,
                                                core.__name__: core,
                                                config_module.__name__: config_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        live_config = extract_function("_brain_ai.py", "_live_config", {"os": os})
        self.brain = SimpleNamespace()
        self.brain._live_config = lambda: live_config(self.brain)

    def test_all_scoped_model_arguments_are_live(self):
        for name, expected in {**MIXINS, "video_analysis.py": 9}.items():
            with self.subTest(file=name):
                source = tree(name)
                stale = [n for n in ast.walk(source) if isinstance(n, ast.Name)
                         and n.id in {"MODEL_BRAIN", "MODEL_VISION"}]
                self.assertEqual(stale, [])
                expressions = list(model_exprs(name))
                # The main decision additionally uses the live fast-model expression.
                self.assertEqual(len(expressions), expected + (name == "_brain_loop.py"))
                for expr in expressions:
                    self.assertTrue(any(isinstance(n, ast.Call)
                                        and isinstance(n.func, ast.Attribute)
                                        and n.func.attr == "_live_config"
                                        for n in ast.walk(expr)), ast.unparse(expr))

    def test_actual_model_expressions_follow_live_values_and_env_fallback(self):
        with patch.dict(os.environ, {"BILI_AI_MODEL_BRAIN": "env-brain",
                                    "BILI_AI_MODEL_VISION": "env-vision"}):
            for current, expected in (("before", ("brain-before", "vision-before")),
                                      ("after", ("brain-after", "vision-after")),
                                      ("empty", ("env-brain", "env-vision"))):
                if current == "after":
                    self.cfg["api"].update(model_brain="brain-after", model_vision="vision-after")
                elif current == "empty":
                    self.cfg["api"].update(model_brain="", model_vision="")
                for name in [*MIXINS, "video_analysis.py"]:
                    for expr in model_exprs(name):
                        ns = {"self": self.brain, "brain": self.brain,
                              "config": self.cfg, "_smart_token": False}
                        value = eval_expr(expr, ns)
                        self.assertIn(value, expected, (name, ast.unparse(expr)))
                        if "model_vision" in ast.unparse(expr):
                            self.assertEqual(value, expected[1])
                        else:
                            self.assertEqual(value, expected[0])

    def test_fast_model_remains_preferred_and_fallback_is_live(self):
        expr = next(e for e in model_exprs("_brain_loop.py")
                    if isinstance(e, ast.IfExp))
        self.cfg["models"] = {"fast": "fast-model"}
        ns = {"self": self.brain, "config": self.cfg, "_smart_token": True}
        self.assertEqual(eval_expr(expr, ns), "fast-model")
        self.cfg["models"]["fast"] = ""
        self.cfg["api"]["model_brain"] = "updated-brain"
        self.assertEqual(eval_expr(expr, ns), "updated-brain")
        ns["_smart_token"] = False
        self.cfg["models"]["fast"] = "other-fast"
        self.assertEqual(eval_expr(expr, ns), "updated-brain")

    def test_parallel_frame_guard_uses_current_flag(self):
        source = tree("_brain_video.py")
        guards = [n.test for n in ast.walk(source) if isinstance(n, ast.If)
                  and any(isinstance(x, ast.Name) and x.id == "should_extract" for x in ast.walk(n.test))
                  and "frames_enabled" in ast.unparse(n.test)]
        self.assertEqual(len(guards), 1)
        self.assertFalse(any(isinstance(n, ast.Name) and n.id == "VISION_FRAMES_ENABLED"
                             for n in ast.walk(source)))
        self.brain._is_vision_globally_disabled = lambda: False
        ns = {"self": self.brain, "config": self.cfg, "do_vision": True, "should_extract": True}
        self.assertTrue(eval_expr(guards[0], ns))  # default enabled
        self.cfg["vision"] = {"frames_enabled": False}
        self.assertFalse(eval_expr(guards[0], ns))
        self.cfg["vision"]["frames_enabled"] = True
        self.assertTrue(eval_expr(guards[0], ns))
        ns["should_extract"] = False
        self.assertFalse(eval_expr(guards[0], ns))
        ns["should_extract"] = True
        self.brain._is_vision_globally_disabled = lambda: True
        self.assertFalse(eval_expr(guards[0], ns))

    def test_manual_cross_check_calls_its_own_brain_with_latest_model(self):
        fn = extract_function("video_analysis.py", "_ai_cross_check_subtitle_match",
                              {"json": json, "log": lambda *args: None})
        seen = []

        async def call(**kwargs):
            seen.append(kwargs["model"])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"match": true, "confidence": 0.9}'))])

        self.brain._call_ai_with_retry = call
        asyncio.run(fn(self.brain, "title", "subtitle " * 10))
        self.cfg["api"]["model_brain"] = "brain-after"
        asyncio.run(fn(self.brain, "title", "subtitle " * 10))
        self.assertEqual(seen, ["brain-before", "brain-after"])


if __name__ == "__main__":
    unittest.main()
