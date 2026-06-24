import sys
import threading
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

    def test_build_output_filename_adds_zero_padded_sequence_prefix(self):
        chapter = app.Chapter(title="上篇 第一章 认识金钱", text="正文", sequence=7)

        filename = app.build_output_filename(chapter)

        self.assertEqual(filename, "007-上篇 第一章 认识金钱.mp3")

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


if __name__ == "__main__":
    unittest.main()
