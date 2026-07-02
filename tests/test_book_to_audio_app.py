import sys
import asyncio
import json
import threading
import tempfile
import types
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT_DIR / "work" / "pydeps"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))

mobi_stub = types.ModuleType("mobi")
mobi_stub.extract = lambda *_args, **_kwargs: ("", "")
sys.modules.setdefault("mobi", mobi_stub)

bs4_stub = types.ModuleType("bs4")
bs4_stub.BeautifulSoup = None
sys.modules.setdefault("bs4", bs4_stub)

ebooklib_stub = types.ModuleType("ebooklib")
ebooklib_epub_stub = types.ModuleType("ebooklib.epub")
ebooklib_stub.ITEM_DOCUMENT = object()
ebooklib_stub.epub = ebooklib_epub_stub
sys.modules.setdefault("ebooklib", ebooklib_stub)
sys.modules.setdefault("ebooklib.epub", ebooklib_epub_stub)

pypdf_stub = types.ModuleType("pypdf")
pypdf_stub.PdfReader = object
sys.modules.setdefault("pypdf", pypdf_stub)

edge_tts_stub = types.ModuleType("edge_tts")
sys.modules.setdefault("edge_tts", edge_tts_stub)

webview_stub = types.ModuleType("webview")
sys.modules.setdefault("webview", webview_stub)

import book_to_audio_app as app


class BookToAudioAppTests(unittest.TestCase):
    def test_voice_options_include_english_female_and_male(self):
        voice_ids = {voice_id for voice_id, _label in app.VOICE_OPTIONS}

        self.assertIn("en-US-JennyNeural", voice_ids)
        self.assertIn("en-US-AriaNeural", voice_ids)
        self.assertIn("en-US-GuyNeural", voice_ids)
        self.assertIn("en-US-DavisNeural", voice_ids)

    def test_html_fallback_extracts_title_and_text_without_bs4(self):
        text, title = app.html_fragment_to_text_and_title("<html><body><h1>第一章 认识金钱</h1><p>正文内容</p></body></html>")

        self.assertEqual(title, "第一章 认识金钱")
        self.assertIn("正文内容", text)

    def test_split_structured_chapters_preserves_part_and_chapter_order(self):
        source = [
            app.Chapter(
                title="上篇",
                text="上篇\n第一章 认识金钱\n第一章的正文。\n第二章 学会储蓄\n第二章的正文。\n下篇\n第四章 开始投资\n第四章的正文。",
            )
        ]

        chunks = app.split_structured_chapters(source)

        self.assertEqual(
            [item.title for item in chunks],
            ["上篇 第一章 认识金钱", "上篇 第二章 学会储蓄", "下篇 第四章 开始投资"],
        )

    def test_split_structured_text_merges_standalone_chapter_heading_with_following_title(self):
        chunks = app.split_structured_text("章节", "第一章\n认识金钱\n这里是正文。")

        self.assertEqual([item.title for item in chunks], ["第一章 认识金钱"])
        self.assertEqual(chunks[0].text, "这里是正文。")

    def test_build_output_filename_adds_zero_padded_sequence_prefix(self):
        chapter = app.Chapter(title="上篇 第一章 认识金钱", text="正文", sequence=7)

        filename = app.build_output_filename(chapter)

        self.assertEqual(filename, "007-上篇 第一章 认识金钱.mp3")

    def test_split_long_text_hard_splits_oversized_plain_text(self):
        text = "A" * 5000

        chunks = app.split_long_text(text, limit=2400)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 2400 for chunk in chunks))
        self.assertEqual("".join(chunk.replace("\n", "") for chunk in chunks), text)

    def test_normalize_text_reflows_single_character_lines(self):
        text = "他\n走\n进\n房\n间。\n她\n说\n你\n好。"

        normalized = app.normalize_text(text)

        self.assertEqual(normalized, "他走进房间。\n她说你好。")

    def test_normalize_text_preserves_heading_line_while_reflowing_body(self):
        text = "第一章\n他\n来\n了。"

        normalized = app.normalize_text(text)

        self.assertEqual(normalized, "第一章\n他来了。")

    def test_normalize_text_keeps_short_standalone_title_line_before_body(self):
        text = "第一章\n认识金钱\n这里是正文。"

        normalized = app.normalize_text(text)

        self.assertEqual(normalized, "第一章\n认识金钱\n这里是正文。")

    def test_app_state_tracks_per_chapter_progress(self):
        state = app.AppState()
        chapters = [
            app.Chapter(title="第一章", text="正文1", sequence=1),
            app.Chapter(title="第二章", text="正文2", sequence=2),
        ]

        state.set_active_chapters(chapters)
        state.update_chapter_progress(1, 35, "processing")
        state.update_chapter_progress(2, 100, "done")
        snapshot = state.snapshot()

        self.assertEqual(snapshot["chapter_progress"]["1"]["percent"], 35)
        self.assertEqual(snapshot["chapter_progress"]["1"]["status"], "processing")
        self.assertEqual(snapshot["chapter_progress"]["2"]["percent"], 100)
        self.assertEqual(snapshot["chapter_progress"]["2"]["status"], "done")

    def test_set_active_chapters_preserves_existing_selection_progress(self):
        state = app.AppState()
        original = [
            app.Chapter(title="第一章", text="正文1", sequence=1),
            app.Chapter(title="第二章", text="正文2", sequence=2),
            app.Chapter(title="第三章", text="正文3", sequence=3),
        ]
        state.set_active_chapters(original)
        state.update_chapter_progress(2, 40, "processing")

        selected = [original[1], original[2]]
        state.set_active_chapters(selected)
        snapshot = state.snapshot()

        self.assertEqual(snapshot["chapter_progress"]["2"]["percent"], 40)
        self.assertEqual(snapshot["chapter_progress"]["2"]["status"], "processing")
        self.assertEqual(snapshot["chapter_progress"]["3"]["status"], "queued")

    def test_progress_methods_do_not_deadlock_inside_state_lock(self):
        state = app.AppState()
        chapter = app.Chapter(title="第一章", text="正文", sequence=1)
        finished = threading.Event()

        def target():
            with state.lock:
                state.set_active_chapters([chapter])
                state.reset_chapter_progress()
            finished.set()

        worker = threading.Thread(target=target, daemon=True)
        worker.start()

        self.assertTrue(finished.wait(1.0))

    def test_synthesize_chapters_to_files_marks_only_failed_chapter(self):
        chapters = [
            app.Chapter(title="第一章", text="ok", sequence=1),
            app.Chapter(title="第二章", text="boom", sequence=2),
        ]
        progress_events = []

        async def fake_stream_text_to_file(text, file_obj, voice, rate, progress=None):
            if text == "boom":
                raise RuntimeError("chapter failed")
            file_obj.write(b"audio")
            if progress:
                progress(100)

        original = app.stream_text_to_file
        app.stream_text_to_file = fake_stream_text_to_file
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaises(RuntimeError):
                    asyncio.run(
                        app.synthesize_chapters_to_files(
                            chapters,
                            Path(temp_dir),
                            "voice",
                            "rate",
                            progress=lambda seq, percent, status: progress_events.append((seq, percent, status)),
                            max_parallel=1,
                        )
                    )
        finally:
            app.stream_text_to_file = original

        self.assertIn((1, 100, "done"), progress_events)
        self.assertIn((2, 0, "error"), progress_events)
        self.assertNotIn((1, 0, "error"), progress_events)

    def test_normalize_generate_request_rejects_invalid_indices(self):
        chapters = [app.Chapter(title="第一章", text="正文", sequence=1)]

        with self.assertRaisesRegex(ValueError, "章节索引无效"):
            app.normalize_generate_request(
                {"indices": [0, -1], "output_dir": "C:/out"},
                chapters,
            )

    def test_normalize_generate_request_rejects_empty_output_dir(self):
        chapters = [app.Chapter(title="第一章", text="正文", sequence=1)]

        with self.assertRaisesRegex(ValueError, "输出目录不能为空"):
            app.normalize_generate_request(
                {"indices": [0], "output_dir": "   "},
                chapters,
            )

    def test_normalize_generate_request_falls_back_for_invalid_voice_and_rate(self):
        chapters = [app.Chapter(title="第一章", text="正文", sequence=1)]

        normalized = app.normalize_generate_request(
            {"indices": [0, 0], "output_dir": "C:/out", "voice": 123, "rate": "fast"},
            chapters,
        )

        self.assertEqual([chapter.sequence for chapter in normalized["selected"]], [1])
        self.assertEqual(normalized["voice"], app.DEFAULT_VOICE)
        self.assertEqual(normalized["rate"], "-12%")

    def test_normalize_preview_request_rejects_invalid_index(self):
        chapters = [app.Chapter(title="第一章", text="正文", sequence=1)]

        with self.assertRaisesRegex(ValueError, "章节索引无效"):
            app.normalize_preview_request({"index": -1}, chapters)

    def test_ensure_chapter_preview_reuses_matching_cache(self):
        chapter = app.Chapter(title="第一章", text="正文", sequence=1)

        async def fake_synthesize(text, output_path, voice, rate):
            output_path.write_bytes(b"fresh-audio")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            audio_path, meta_path = app.chapter_preview_paths(cache_dir, chapter)
            audio_path.write_bytes(b"cached-audio")
            meta_path.write_text(
                json.dumps({"voice": app.DEFAULT_VOICE, "rate": "-12%", "title": chapter.title}, ensure_ascii=False),
                encoding="utf-8",
            )

            entry = app.ensure_chapter_preview(
                cache_dir,
                chapter,
                app.DEFAULT_VOICE,
                "-12%",
                False,
                fake_synthesize,
            )

            self.assertEqual(audio_path.read_bytes(), b"cached-audio")
            self.assertTrue(entry["exists"])
            self.assertEqual(entry["voice"], app.DEFAULT_VOICE)
            self.assertEqual(entry["rate"], "-12%")

    def test_ensure_chapter_preview_force_regenerates_cache(self):
        chapter = app.Chapter(title="第一章", text="正文", sequence=1)
        calls = []

        async def fake_synthesize(text, output_path, voice, rate):
            calls.append((text, voice, rate))
            output_path.write_bytes(b"fresh-audio")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            audio_path, meta_path = app.chapter_preview_paths(cache_dir, chapter)
            audio_path.write_bytes(b"cached-audio")
            meta_path.write_text(
                json.dumps({"voice": app.DEFAULT_VOICE, "rate": "-12%", "title": chapter.title}, ensure_ascii=False),
                encoding="utf-8",
            )

            entry = app.ensure_chapter_preview(
                cache_dir,
                chapter,
                app.DEFAULT_VOICE,
                "-12%",
                True,
                fake_synthesize,
            )

            self.assertEqual(calls, [("正文", app.DEFAULT_VOICE, "-12%")])
            self.assertEqual(audio_path.read_bytes(), b"fresh-audio")
            self.assertEqual(entry["voice"], app.DEFAULT_VOICE)

    def test_clear_chapter_preview_removes_cached_files(self):
        chapter = app.Chapter(title="第一章", text="正文", sequence=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            audio_path, meta_path = app.chapter_preview_paths(cache_dir, chapter)
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(b"cached-audio")
            meta_path.write_text("{}", encoding="utf-8")

            removed = app.clear_chapter_preview(cache_dir, chapter)

            self.assertTrue(removed)
            self.assertFalse(audio_path.exists())
            self.assertFalse(meta_path.exists())


if __name__ == "__main__":
    unittest.main()
