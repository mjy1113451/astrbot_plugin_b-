"""Run the real video-understanding coroutine with Event-driven fake workers."""
import ast
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_owned_async_tasks import drain_shielded
from test_brain_lifecycle import load_node

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "brain" / "_brain_video.py"
PACKAGE = "_video_task_cleanup_fixture"


def extract_understand(namespace):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    mixin = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "BrainVideoMixin")
    function = next(node for node in mixin.body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "_understand_super_smart")
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace[function.name]


class FakeWorker:
    def __init__(self, name, result, history):
        self.name = name
        self.result = result
        self.error = None
        self.history = history
        self.task = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()
        self.finished = asyncio.Event()

    async def run(self, *args, **kwargs):
        self.task = asyncio.current_task()
        self.started.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return self.result
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.history.append(f"{self.name}_finished")
            self.finished.set()


class TrackedMedia:
    def __init__(self, path, workers, history):
        self.path = path
        self.name = path.name
        self.path.touch()
        self.workers = workers
        self.history = history
        self.deleted = False
        self.finished_when_deleted = None

    def exists(self):
        return self.path.exists()

    def unlink(self):
        self.finished_when_deleted = tuple(worker.finished.is_set() for worker in self.workers)
        self.history.append("media_deleted")
        self.path.unlink()
        self.deleted = True


class VideoTaskCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.history = []
        self.parent = None
        self.asr_result = SimpleNamespace(
            success=True, text="asr result", segments=[SimpleNamespace(speaker="speaker")],
            timing={},
        )
        self.asr = FakeWorker("asr", self.asr_result, self.history)
        self.vision = FakeWorker("vision", "vision result", self.history)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.media = TrackedMedia(Path(directory.name) / "shared.mp4", (self.asr, self.vision), self.history)
        self.engine = SimpleNamespace(
            is_available=Mock(return_value=True), has_ffmpeg=Mock(return_value=True),
            process_video=self.asr.run, format_result=Mock(return_value="asr result"),
        )
        self.engine_type = SimpleNamespace(
            should_skip_asr=Mock(return_value=(False, "")),
            timing_summary=Mock(return_value="fake timing summary"),
        )
        modules = {}
        for name in (PACKAGE, f"{PACKAGE}.brain", f"{PACKAGE}.xingye_bot"):
            module = ModuleType(name)
            module.__path__ = []
            modules[name] = module
        engine_module = ModuleType(f"{PACKAGE}.xingye_bot.asr_engine")
        engine_module.ASREngine = self.engine_type
        engine_module.get_asr_engine = Mock(return_value=self.engine)
        modules[engine_module.__name__] = engine_module
        module_patch = patch.dict(sys.modules, modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        self.path_factory = Mock(return_value=self.media)
        self.log_exception = Mock()
        self.understand = extract_understand({
            "__package__": f"{PACKAGE}.brain", "asyncio": asyncio,
            "config": {"asr": {}, "vision": {"frames_enabled": True}},
            "ASR_ENABLED": True, "SUBTITLE_STRICT_CHECK": False,
            "_Path": self.path_factory, "log": Mock(), "log_exception": self.log_exception,
            "drain_shielded": drain_shielded,
        })
        self.brain = SimpleNamespace(
            _download_video_for_asr=AsyncMock(return_value=(self.media.name, 0.1, 1.0)),
            _ai_decide_frame_count=AsyncMock(return_value=(True, 3, "fake frames")),
            _is_vision_globally_disabled=Mock(return_value=False),
            _extract_and_analyze_frames=self.vision.run,
            _understand_with_vision_frames=AsyncMock(return_value=None),
        )

    async def asyncTearDown(self):
        # Own only these fixture tasks; also clean up after a red-test assertion.
        for worker in (self.asr, self.vision):
            worker.release.set()
            worker.cleanup_release.set()
        tasks = [task for task in (self.parent, self.asr.task, self.vision.task)
                 if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def start_parent(self):
        self.parent = asyncio.create_task(self.understand(
            self.brain, "BV1Q541167Qg", "fake title", force_mode="asr+vision",
        ))
        return self.parent

    async def wait_event(self, event):
        await asyncio.wait_for(event.wait(), timeout=2)

    async def start_parallel(self):
        self.start_parent()
        await asyncio.wait_for(asyncio.gather(
            self.asr.started.wait(), self.vision.started.wait(),
        ), timeout=2)

    def assert_workers_finished_before_delete(self):
        self.assertTrue(self.media.deleted)
        self.assertFalse(self.media.path.exists())
        self.assertEqual(self.media.finished_when_deleted, (True, True))
        self.assertEqual(self.history[-1], "media_deleted")
        self.assertTrue(self.asr.task.done())
        self.assertTrue(self.vision.task.done())

    async def assert_cancellation_drains_workers(self, cancel_child=False, repeat=False):
        self.vision.cleanup_release.clear()
        await self.start_parallel()
        if cancel_child:
            self.asr.task.cancel()
        else:
            self.parent.cancel()
        await self.wait_event(self.asr.finished)
        await self.wait_event(self.vision.cleanup_started)
        self.assertTrue(self.asr.cancelled.is_set())
        self.assertTrue(self.vision.cancelled.is_set())
        self.assertFalse(self.vision.finished.is_set())
        self.assertFalse(self.parent.done())
        self.assertFalse(self.media.deleted)
        if repeat:
            self.parent.cancel()
            await asyncio.sleep(0)
            self.assertFalse(self.parent.done())
            self.assertEqual(self.vision.task.cancelling(), 1)
            self.assertTrue(self.media.path.exists())
        self.vision.cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.parent, timeout=2)
        self.assert_workers_finished_before_delete()
        self.assertTrue(self.asr.task.cancelled())
        self.assertTrue(self.vision.task.cancelled())
        self.brain._understand_with_vision_frames.assert_not_awaited()
        self.log_exception.assert_not_called()

    async def test_parent_cancel_while_awaiting_asr_drains_vision_before_media_cleanup(self):
        await self.assert_cancellation_drains_workers(repeat=True)

    async def test_repeated_parent_cancel_while_awaiting_vision_preserves_media_cleanup(self):
        asr_consumed = asyncio.Event()
        self.engine.format_result.side_effect = lambda _: (asr_consumed.set() or "asr result")
        self.vision.cleanup_release.clear()
        await self.start_parallel()
        self.asr.release.set()
        await self.wait_event(asr_consumed)
        self.parent.cancel()
        await self.wait_event(self.vision.cleanup_started)
        self.parent.cancel()
        await asyncio.sleep(0)
        self.assertFalse(self.parent.done())
        self.assertEqual(self.vision.task.cancelling(), 1)
        self.assertTrue(self.media.path.exists())
        self.vision.cleanup_release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.parent, timeout=2)
        self.assert_workers_finished_before_delete()

    async def test_cancel_waits_for_asr_executor_before_deleting_video_and_audio(self):
        extraction_started = asyncio.Event()
        release_extraction = threading.Event()
        loop = asyncio.get_running_loop()
        audio_path = self.media.path.with_suffix(".wav")

        def extract_audio(video_path, _):
            loop.call_soon_threadsafe(extraction_started.set)
            if not release_extraction.wait(timeout=5):
                raise TimeoutError("test extraction not released")
            self.assertTrue(video_path.exists())
            audio_path.touch()
            return audio_path, 0.1

        process_video = load_node("xingye_bot/asr_engine.py", "process_video", {
            "Path": Path, "ASRResult": SimpleNamespace, "asyncio": asyncio,
            "drain_shielded": drain_shielded,
        })
        engine = SimpleNamespace(extract_audio=extract_audio, _cleanup_audio=lambda path: path.unlink())

        async def run_asr(*args, **kwargs):
            self.asr.task = asyncio.current_task()
            try:
                return await process_video(engine, *args, **kwargs)
            finally:
                self.asr.finished.set()

        self.engine.process_video = run_asr
        self.start_parent()
        try:
            await self.wait_event(extraction_started)
            self.parent.cancel()
            await self.wait_event(self.vision.finished)
            self.parent.cancel()
            await asyncio.sleep(0)
            self.assertFalse(self.parent.done())
            self.assertTrue(self.media.path.exists())
        finally:
            release_extraction.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.parent, timeout=2)
        self.assert_workers_finished_before_delete()
        self.assertFalse(audio_path.exists())

    async def test_asr_child_cancel_drains_vision_without_parent_cancel_request(self):
        await self.assert_cancellation_drains_workers(cancel_child=True)
        self.assertEqual(self.parent.cancelling(), 0)

    async def test_normal_parallel_results_keep_original_return_and_timing(self):
        await self.start_parallel()
        self.asr.release.set()
        self.vision.release.set()
        self.assertEqual(await asyncio.wait_for(self.parent, timeout=2), (
            True, "【ASR语音识别】\nasr result\n\n---\n\n【视觉画面理解】\nvision result",
        ))
        self.assert_workers_finished_before_delete()
        self.assertIn("total_elapsed_seconds", self.asr_result.timing)
        self.engine_type.timing_summary.assert_called_once_with(
            self.asr_result, download_sec=0.1, download_size_mb=1.0,
        )
        self.assertFalse(self.asr.cancelled.is_set())
        self.assertFalse(self.vision.cancelled.is_set())
        self.log_exception.assert_not_called()

    async def assert_regular_failure_keeps_partner(self, failed, partner, expected):
        error = RuntimeError(f"fake {failed.name} failure")
        failed.error = error
        await self.start_parallel()
        failed.release.set()
        await self.wait_event(failed.finished)
        self.assertFalse(partner.cancelled.is_set())
        self.assertFalse(partner.task.done())
        self.assertFalse(self.media.deleted)
        partner.release.set()
        self.assertEqual(await asyncio.wait_for(self.parent, timeout=2), (True, expected))
        self.assert_workers_finished_before_delete()
        self.assertFalse(partner.cancelled.is_set())
        self.log_exception.assert_called_once_with(
            "ASR 识别异常" if failed is self.asr else "视觉帧理解异常", error,
        )
        self.brain._understand_with_vision_frames.assert_not_awaited()

    async def test_regular_asr_error_keeps_vision_result(self):
        await self.assert_regular_failure_keeps_partner(
            self.asr, self.vision, "【视觉画面理解】\nvision result",
        )

    async def test_regular_vision_error_keeps_asr_result(self):
        await self.assert_regular_failure_keeps_partner(
            self.vision, self.asr, "【ASR语音识别】\nasr result",
        )

    async def test_both_regular_errors_keep_original_failure_tuple(self):
        self.asr.error = RuntimeError("fake asr failure")
        self.vision.error = RuntimeError("fake vision failure")
        await self.start_parallel()
        self.asr.release.set()
        self.vision.release.set()
        self.assertEqual(await asyncio.wait_for(self.parent, timeout=2), (
            False, "【理解失败】标题: fake title\n分区: \n时长: 0s",
        ))
        self.assert_workers_finished_before_delete()
        self.assertEqual(self.log_exception.call_count, 2)
        self.brain._understand_with_vision_frames.assert_not_awaited()

    async def assert_cancel_before_parallel(self, stage):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def block(*args, **kwargs):
            entered.set()
            await release.wait()

        operation = (self.brain._download_video_for_asr if stage == "download"
                     else self.brain._ai_decide_frame_count)
        operation.side_effect = block
        self.start_parent()
        await self.wait_event(entered)
        self.parent.cancel()
        # Cancellation must propagate, not be replaced by an unbound task variable.
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.parent, timeout=2)
        self.assertIsNone(self.asr.task)
        self.assertIsNone(self.vision.task)
        self.assertEqual(self.media.deleted, stage == "frames")
        self.assertEqual(self.history, ["media_deleted"] if stage == "frames" else [])
        if stage == "download":
            self.path_factory.assert_not_called()
        self.log_exception.assert_not_called()
        self.brain._understand_with_vision_frames.assert_not_awaited()

    async def test_cancel_during_download_before_workers_exist(self):
        await self.assert_cancel_before_parallel("download")

    async def test_cancel_during_frame_decision_before_workers_exist(self):
        await self.assert_cancel_before_parallel("frames")


if __name__ == "__main__":
    unittest.main()
