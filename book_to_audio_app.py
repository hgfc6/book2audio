import asyncio
import hashlib
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse


BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIRS = [
    BASE_DIR / "work" / "pydeps_clean",
    BASE_DIR / "work" / "pydeps",
]
for vendor_dir in reversed(VENDOR_DIRS):
    if vendor_dir.exists() and str(vendor_dir) not in sys.path:
        sys.path.insert(0, str(vendor_dir))

try:
    import mobi  # type: ignore
except Exception:
    mobi = None  # type: ignore[assignment]
try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:
    BeautifulSoup = None  # type: ignore[assignment]
try:
    from ebooklib import ITEM_DOCUMENT, epub  # type: ignore
except Exception:
    ITEM_DOCUMENT = None  # type: ignore[assignment]
    epub = None  # type: ignore[assignment]
try:
    from pypdf import PdfReader  # type: ignore
except Exception:
    PdfReader = None  # type: ignore[assignment]
import edge_tts  # type: ignore
import webview  # type: ignore


SUPPORTED_EXTS = {".epub", ".mobi", ".pdf"}
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_OUTPUT_DIR = BASE_DIR / "outputs"
PREVIEW_CACHE_ROOT = Path(tempfile.gettempdir()) / "book_to_audio_previews"
VOICE_OPTIONS = [
    ("zh-CN-XiaoxiaoNeural", "中文女生 · 晓晓"),
    ("zh-CN-XiaoyiNeural", "中文女生 · 晓伊"),
    ("zh-CN-liaoning-XiaobeiNeural", "中文女生 · 辽宁晓北"),
    ("zh-CN-shaanxi-XiaoniNeural", "中文女生 · 陕西晓妮"),
    ("zh-CN-YunxiNeural", "中文男生 · 云希"),
    ("zh-CN-YunjianNeural", "中文男生 · 云健"),
    ("zh-CN-YunxiaNeural", "中文男生 · 云夏"),
    ("zh-CN-YunyangNeural", "中文男生 · 云扬"),
    ("en-US-JennyNeural", "English Female · Jenny"),
    ("en-US-AriaNeural", "English Female · Aria"),
    ("en-US-GuyNeural", "English Male · Guy"),
    ("en-US-DavisNeural", "English Male · Davis"),
]
VALID_VOICE_IDS = {voice_id for voice_id, _label in VOICE_OPTIONS}
VALID_RATES = {"-20%", "-12%", "-5%", "+0%", "+10%"}
VALID_OUTPUT_MODES = {"per_chapter", "single_file"}
MAX_PARALLEL_CHAPTERS = 3
SPECIAL_HEADING_RE = re.compile(r"^(序|序章|前言|引言|楔子|后记|尾声|附录|番外|终章)$")
PART_HEADING_RE = re.compile(
    r"^(上篇|中篇|下篇|终篇|第[一二三四五六七八九十百千0-9]+[篇卷部册集辑](?:$|[\s:：\-][^\n]{0,30}))$"
)
CHAPTER_HEADING_RE = re.compile(r"^(第[一二三四五六七八九十百千0-9]+章(?:$|[\s:：\-][^\n]{0,40}))$")
SECTION_HEADING_RE = re.compile(r"^(第[一二三四五六七八九十百千0-9]+节(?:$|[\s:：\-][^\n]{0,40}))$")

HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Book To Audio</title>
  <style>
    :root {
      --bg: #f5f1e8;
      --panel: #fffdf8;
      --line: #d7cdbb;
      --ink: #1d2a22;
      --muted: #5f6b62;
      --accent: #2f6b53;
      --accent-soft: #dfeadf;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(47,107,83,0.08), transparent 35%),
        linear-gradient(180deg, #f8f4ec, var(--bg));
      color: var(--ink);
    }
    .wrap {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      margin-bottom: 20px;
      padding: 24px 28px;
      background: linear-gradient(135deg, #264f3f, #3d765b);
      color: #fdf9f0;
      border-radius: 22px;
      box-shadow: 0 16px 40px rgba(22, 38, 31, 0.18);
    }
    .hero h1 {
      margin: 0 0 8px;
      font-size: 34px;
      letter-spacing: 0.5px;
    }
    .hero p {
      margin: 0;
      color: rgba(253, 249, 240, 0.88);
      line-height: 1.6;
    }
    .grid {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 18px;
      box-shadow: 0 8px 24px rgba(56, 52, 43, 0.06);
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 18px;
    }
    label {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      color: var(--muted);
    }
    input[type=text], select {
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      margin-bottom: 12px;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: start;
    }
    button {
      border: 0;
      border-radius: 12px;
      padding: 11px 16px;
      cursor: pointer;
      font-weight: 700;
      background: var(--accent);
      color: #fffdf8;
    }
    button.secondary {
      background: #ebe4d4;
      color: #2b342e;
    }
    button.ghost {
      background: transparent;
      color: var(--accent);
      border: 1px solid var(--line);
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .button-row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .status {
      margin-top: 8px;
      padding: 12px 14px;
      border-radius: 12px;
      background: var(--accent-soft);
      color: #244535;
      font-size: 14px;
      min-height: 48px;
    }
    .chapter-list {
      max-height: 440px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      padding: 8px;
      margin-bottom: 12px;
    }
    .chapter-item {
      position: relative;
      display: grid;
      grid-template-columns: 24px 1fr auto;
      gap: 10px;
      padding: 12px 10px;
      border-bottom: 1px solid #efe7d8;
      align-items: start;
      overflow: hidden;
    }
    .chapter-item:last-child {
      border-bottom: 0;
    }
    .chapter-fill {
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      background: linear-gradient(90deg, rgba(47,107,83,0.18), rgba(47,107,83,0.06));
      transition: width 0.35s ease;
      pointer-events: none;
    }
    .chapter-text, .chapter-meta, .chapter-check {
      position: relative;
      z-index: 1;
    }
    .chapter-text {
      min-width: 0;
    }
    .chapter-name {
      font-weight: 700;
      line-height: 1.45;
    }
    .chapter-meta {
      text-align: right;
      min-width: 86px;
      font-size: 12px;
      color: var(--muted);
    }
    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 10px;
    }
    .preview-panel {
      margin-bottom: 14px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #faf7f0;
    }
    .preview-panel audio {
      width: 100%;
      margin: 8px 0;
    }
    .preview-title {
      font-weight: 700;
      line-height: 1.45;
    }
    .muted {
      color: var(--muted);
      font-size: 13px;
    }
    .chapter-actions {
      position: relative;
      z-index: 1;
      min-width: 220px;
    }
    .chapter-preview {
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
      text-align: right;
    }
    .chapter-buttons {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    button.mini {
      padding: 7px 10px;
      font-size: 12px;
      border-radius: 10px;
    }
    @media (max-width: 920px) {
      .grid { grid-template-columns: 1fr; }
      .hero h1 { font-size: 28px; }
      .chapter-item { grid-template-columns: 24px 1fr; }
      .chapter-actions { grid-column: 1 / -1; min-width: 0; }
      .chapter-preview { text-align: left; }
      .chapter-buttons { justify-content: flex-start; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Book To Audio</h1>
      <p>导入 epub / mobi / pdf，解析章节，选择输出目录，然后生成中文 MP3 音频。界面为内嵌桌面客户端，本地运行。</p>
    </section>

    <div class="grid">
      <section class="panel">
        <h2>输入与输出</h2>
        <label>图书文件</label>
        <div class="row">
          <input id="filePath" type="text" placeholder="请选择 epub / mobi / pdf 文件" />
          <button class="secondary" onclick="pickFile()">选择文件</button>
        </div>

        <label>输出目录</label>
        <div class="row">
          <input id="outputDir" type="text" placeholder="请选择输出目录" />
          <button class="secondary" onclick="pickFolder()">选择目录</button>
        </div>

        <label>语音</label>
        <select id="voice">
          <option value="zh-CN-XiaoxiaoNeural">中文女生 · 晓晓</option>
          <option value="zh-CN-XiaoyiNeural">中文女生 · 晓伊</option>
          <option value="zh-CN-liaoning-XiaobeiNeural">中文女生 · 辽宁晓北</option>
          <option value="zh-CN-shaanxi-XiaoniNeural">中文女生 · 陕西晓妮</option>
          <option value="zh-CN-YunxiNeural">中文男生 · 云希</option>
          <option value="zh-CN-YunjianNeural">中文男生 · 云健</option>
          <option value="zh-CN-YunxiaNeural">中文男生 · 云夏</option>
          <option value="zh-CN-YunyangNeural">中文男生 · 云扬</option>
          <option value="en-US-JennyNeural">English Female · Jenny</option>
          <option value="en-US-AriaNeural">English Female · Aria</option>
          <option value="en-US-GuyNeural">English Male · Guy</option>
          <option value="en-US-DavisNeural">English Male · Davis</option>
        </select>

        <label>语速</label>
        <select id="rate">
          <option value="-20%">偏慢</option>
          <option value="-12%" selected>播客风</option>
          <option value="-5%">略慢</option>
          <option value="+0%">正常</option>
          <option value="+10%">偏快</option>
        </select>

        <label>输出模式</label>
        <select id="outputMode">
          <option value="per_chapter">每章一个 MP3</option>
          <option value="single_file">整本合并成一个 MP3</option>
        </select>

        <div class="button-row">
          <button onclick="loadBook()">解析章节</button>
          <button class="ghost" onclick="selectAll()">全选章节</button>
          <button class="ghost" onclick="clearAll()">清空选择</button>
        </div>

        <div class="status" id="status">请选择图书文件，然后点击“解析章节”。</div>
      </section>

      <section class="panel">
        <div class="section-title">
          <h2>章节选择</h2>
          <div class="button-row">
            <button onclick="generateSelected()">生成选中章节</button>
            <button class="secondary" onclick="generateAll()">生成全部章节</button>
          </div>
        </div>
        <div class="preview-panel">
          <div class="preview-title" id="previewTitle">试听播放器</div>
          <audio id="previewPlayer" controls preload="metadata"></audio>
          <div class="muted" id="previewHint">点击章节上的“朗读”生成临时试听音频。试听音频会保留到你手动点击“清理”为止。</div>
        </div>
        <div class="chapter-list" id="chapterList"></div>
      </section>
    </div>
  </div>

  <script>
    let chapters = [];
    let state = { busy: false };
    let pollTimer = null;
    let previewBusyIndex = null;
    let previewBusyAction = "";
    let currentPreviewIndex = null;

    function setStatus(text) {
      document.getElementById("status").textContent = text;
    }

    function chapterProgressMap() {
      return state.chapter_progress || {};
    }

    function previewCacheMap() {
      return state.preview_cache || {};
    }

    function selectedIndices() {
      return Array.from(document.querySelectorAll(".chapter-check"))
        .filter(x => x.checked)
        .map(x => Number(x.value));
    }

    function renderChapters(items) {
      chapters = items;
      const box = document.getElementById("chapterList");
      if (!items.length) {
        box.innerHTML = "<div class='muted'>没有解析到章节。</div>";
        return;
      }
      const checked = new Set(selectedIndices());
      const progressMap = chapterProgressMap();
      box.innerHTML = items.map((item, idx) => `
        <label class="chapter-item">
          <div class="chapter-fill" style="width:${Number((progressMap[String(item.sequence)] || {}).percent || 0)}%"></div>
          <input class="chapter-check" type="checkbox" value="${idx}" ${checked.has(idx) ? "checked" : ""} />
          <div class="chapter-text">
            <div class="chapter-name">${String(item.sequence || idx + 1).padStart(3, "0")}. ${escapeHtml(item.title)}</div>
          </div>
          <div class="chapter-actions">
            <div class="chapter-meta">${renderChapterMeta(item.sequence)}</div>
            <div class="chapter-preview">${renderPreviewMeta(item.sequence, idx)}</div>
            <div class="chapter-buttons">
              <button type="button" class="secondary mini" onclick="instantRead(event, ${idx})" ${previewBusyIndex === idx ? "disabled" : ""}>立即朗读</button>
              <button type="button" class="ghost mini" onclick="playChapter(event, ${idx})">${renderPlayButtonLabel(idx)}</button>
              <button type="button" class="secondary mini" onclick="regeneratePreview(event, ${idx})" ${previewBusyIndex === idx ? "disabled" : ""}>重新生成</button>
              <button type="button" class="ghost mini" onclick="clearPreview(event, ${idx})" ${renderClearButtonDisabled(item.sequence, idx)}>清理</button>
            </div>
          </div>
        </label>
      `).join("");
    }

    function renderChapterMeta(sequence) {
      const chapterState = chapterProgressMap()[String(sequence)] || { percent: 0, status: "idle" };
      const percent = Number(chapterState.percent || 0);
      const status = chapterState.status || "idle";
      if (status === "done") return "100% 已完成";
      if (status === "processing") return `${percent}% 转换中`;
      if (status === "queued") return "等待中";
      if (status === "error") return "失败";
      return "未开始";
    }

    function renderPreviewMeta(sequence, idx) {
      if (previewBusyIndex === idx) {
        if (previewBusyAction === "instant") return "即时朗读：正在连接";
        return previewBusyAction === "regenerate" ? "试听：正在重新生成" : "试听：正在准备";
      }
      const preview = previewCacheMap()[String(sequence)];
      if (!preview || !preview.exists) return "试听：未生成";
      const currentVoice = document.getElementById("voice").value;
      const currentRate = document.getElementById("rate").value;
      if (preview.voice === currentVoice && preview.rate === currentRate) {
        return `试听：已缓存 · ${escapeHtml(preview.voice)} · ${escapeHtml(preview.rate)}`;
      }
      return `试听：缓存为 ${escapeHtml(preview.voice || "")} ${escapeHtml(preview.rate || "")}`.trim();
    }

    function renderPlayButtonLabel(idx) {
      if (previewBusyIndex === idx) return "准备中";
      if (currentPreviewIndex === idx) return "重新播放";
      return "朗读";
    }

    function renderClearButtonDisabled(sequence, idx) {
      if (previewBusyIndex === idx) return "disabled";
      const preview = previewCacheMap()[String(sequence)];
      return (!preview || !preview.exists) ? "disabled" : "";
    }

    function escapeHtml(text) {
      const div = document.createElement("div");
      div.textContent = text;
      return div.innerHTML;
    }

    function selectAll() {
      document.querySelectorAll(".chapter-check").forEach(x => x.checked = true);
    }

    function clearAll() {
      document.querySelectorAll(".chapter-check").forEach(x => x.checked = false);
    }

    async function postJson(url, payload = {}) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return res.json();
    }

    async function pickFile() {
      setStatus("正在打开文件选择框……");
      const data = await postJson("/api/pick-file");
      if (data.ok && data.path) {
        document.getElementById("filePath").value = data.path;
        setStatus("已选择文件。");
      } else {
        setStatus(data.error || "未选择文件。");
      }
    }

    async function pickFolder() {
      setStatus("正在打开目录选择框……");
      const data = await postJson("/api/pick-folder");
      if (data.ok && data.path) {
        document.getElementById("outputDir").value = data.path;
        setStatus("已选择输出目录。");
      } else {
        setStatus(data.error || "未选择目录。");
      }
    }

    async function loadBook() {
      const path = document.getElementById("filePath").value.trim();
      if (!path) {
        setStatus("请先选择图书文件。");
        return;
      }
      setStatus("正在解析章节……");
      const data = await postJson("/api/load-book", { path });
      if (!data.ok) {
        setStatus(data.error || "解析失败。");
        return;
      }
      state.preview_cache = data.preview_cache || {};
      const player = document.getElementById("previewPlayer");
      player.pause();
      player.removeAttribute("src");
      player.load();
      currentPreviewIndex = null;
      updatePreviewPlayerTitle("试听播放器", "点击章节上的“朗读”生成临时试听音频。试听音频会保留到你手动点击“清理”为止。");
      renderChapters(data.chapters);
      setStatus(`已解析 ${data.chapters.length} 个章节。`);
    }

    async function startGenerate(indices) {
      const filePath = document.getElementById("filePath").value.trim();
      const outputDir = document.getElementById("outputDir").value.trim();
      const voice = document.getElementById("voice").value;
      const rate = document.getElementById("rate").value;
      const outputMode = document.getElementById("outputMode").value;
      if (!filePath || !outputDir) {
        setStatus("请先选择图书文件和输出目录。");
        return;
      }
      if (!indices.length) {
        setStatus("请至少选中一个章节。");
        return;
      }
      const data = await postJson("/api/generate", { indices, output_dir: outputDir, voice, rate, output_mode: outputMode });
      if (!data.ok) {
        setStatus(data.error || "启动生成失败。");
        return;
      }
      setStatus("正在生成音频……");
      startPolling();
    }

    function generateSelected() {
      startGenerate(selectedIndices());
    }

    function generateAll() {
      startGenerate(chapters.map((_, idx) => idx));
    }

    function stopButtonEvent(event) {
      event.preventDefault();
      event.stopPropagation();
    }

    function previewMatchesCurrentSettings(sequence) {
      const preview = previewCacheMap()[String(sequence)];
      if (!preview || !preview.exists) return false;
      return preview.voice === document.getElementById("voice").value && preview.rate === document.getElementById("rate").value;
    }

    function updatePreviewPlayerTitle(text, hint = "") {
      document.getElementById("previewTitle").textContent = text;
      document.getElementById("previewHint").textContent = hint;
    }

    async function playAudioForChapter(idx) {
      const player = document.getElementById("previewPlayer");
      const item = chapters[idx];
      currentPreviewIndex = idx;
      player.src = `/api/preview-audio?index=${idx}&t=${Date.now()}`;
      updatePreviewPlayerTitle(`试听播放器 · ${item.title}`, "可直接拖动进度条跳转。");
      try {
        await player.play();
      } catch (_err) {
      }
      renderChapters(chapters);
    }

    async function ensurePreview(idx, force) {
      const item = chapters[idx];
      previewBusyIndex = idx;
      previewBusyAction = force ? "regenerate" : "play";
      renderChapters(chapters);
      setStatus(force ? `正在重新生成试听：${item.title}` : `正在准备试听：${item.title}`);
      const data = await postJson("/api/preview-chapter", {
        index: idx,
        voice: document.getElementById("voice").value,
        rate: document.getElementById("rate").value,
        force,
      });
      previewBusyIndex = null;
      previewBusyAction = "";
      if (!data.ok) {
        renderChapters(chapters);
        setStatus(data.error || "试听生成失败。");
        return false;
      }
      state.preview_cache = state.preview_cache || {};
      state.preview_cache[String(data.sequence)] = data.preview_cache;
      renderChapters(chapters);
      setStatus(data.reused ? "已使用缓存试听音频。" : "试听音频已生成。");
      return true;
    }

    async function playChapter(event, idx) {
      stopButtonEvent(event);
      const item = chapters[idx];
      if (!previewMatchesCurrentSettings(item.sequence)) {
        const ok = await ensurePreview(idx, false);
        if (!ok) return;
      }
      await playAudioForChapter(idx);
    }

    async function regeneratePreview(event, idx) {
      stopButtonEvent(event);
      const ok = await ensurePreview(idx, true);
      if (!ok) return;
      await playAudioForChapter(idx);
    }

    async function instantRead(event, idx) {
      stopButtonEvent(event);
      const item = chapters[idx];
      previewBusyIndex = idx;
      previewBusyAction = "instant";
      renderChapters(chapters);
      setStatus(`正在连接即时朗读：${item.title}`);
      const data = await postJson("/api/instant-read", {
        index: idx,
        voice: document.getElementById("voice").value,
        rate: document.getElementById("rate").value,
      });
      previewBusyIndex = null;
      previewBusyAction = "";
      if (!data.ok) {
        renderChapters(chapters);
        setStatus(data.error || "即时朗读启动失败。");
        return;
      }
      const player = document.getElementById("previewPlayer");
      currentPreviewIndex = idx;
      player.src = data.url;
      updatePreviewPlayerTitle(`即时朗读 · ${item.title}`, "即时朗读不生成临时文件，进度条不保证可拖动跳转。");
      try {
        await player.play();
      } catch (_err) {
      }
      renderChapters(chapters);
      setStatus("即时朗读已开始。");
    }

    async function clearPreview(event, idx) {
      stopButtonEvent(event);
      const data = await postJson("/api/preview-clear", { index: idx });
      if (!data.ok) {
        setStatus(data.error || "试听缓存清理失败。");
        return;
      }
      if (state.preview_cache) {
        delete state.preview_cache[String(data.sequence)];
      }
      if (currentPreviewIndex === idx) {
        const player = document.getElementById("previewPlayer");
        player.pause();
        player.removeAttribute("src");
        player.load();
        currentPreviewIndex = null;
        updatePreviewPlayerTitle("试听播放器", "当前章节试听缓存已清理。");
      }
      renderChapters(chapters);
      setStatus("试听缓存已清理。");
    }

    async function pollStatus() {
      const res = await fetch("/api/status");
      const data = await res.json();
      state = data;
      if (chapters.length) renderChapters(chapters);
      if (data.status_text) setStatus(data.status_text);
      if (!data.busy && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    function startPolling() {
      if (pollTimer) clearInterval(pollTimer);
      pollStatus();
      pollTimer = setInterval(pollStatus, 1200);
    }

    startPolling();
  </script>
</body>
</html>
"""


@dataclass
class Chapter:
    title: str
    text: str
    sequence: int = 0


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        return "\n".join(self.parts)


def sanitize_filename(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    safe = re.sub(r"\s+", " ", safe)
    return safe[:80] or "output"


def html_fragment_to_text_and_title(content: bytes | str) -> tuple[str, str]:
    raw = content.decode("utf-8", errors="ignore") if isinstance(content, bytes) else content
    if BeautifulSoup is not None:
        soup = BeautifulSoup(raw, "html.parser")
        text = normalize_text(soup.get_text("\n"))
        heading = soup.find(["h1", "h2", "h3", "title"])
        title = normalize_text(heading.get_text(" ")) if heading else ""
        return text, title

    extractor = _HTMLTextExtractor()
    extractor.feed(re.sub(r"(?is)<(script|style)\b.*?</\1>", "", raw))
    text = normalize_text(extractor.get_text())
    title_match = re.search(r"(?is)<(?:h1|h2|h3|title)[^>]*>(.*?)</(?:h1|h2|h3|title)>", raw)
    title = normalize_text(re.sub(r"(?is)<[^>]+>", " ", title_match.group(1))) if title_match else ""
    return text, title


def is_heading_like_line(text: str) -> bool:
    line = text.strip()
    if not line:
        return False
    return bool(
        SPECIAL_HEADING_RE.match(line)
        or PART_HEADING_RE.match(line)
        or CHAPTER_HEADING_RE.match(line)
        or SECTION_HEADING_RE.match(line)
    )


def should_reflow_lines(current: str, next_line: str) -> bool:
    left = current.strip()
    right = next_line.strip()
    if not left or not right:
        return False
    if is_heading_like_line(left) or is_heading_like_line(right):
        return False
    if re.search(r"[。！？?!；;]$", left):
        return False
    if min(len(left), len(right)) <= 2:
        return True
    return len(left) <= 3 and len(right) <= 3


def reflow_broken_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    current = ""
    for line in lines:
        if not current:
            current = line
            continue
        if should_reflow_lines(current, line):
            current += line
        else:
            merged.append(current)
            current = line
    if current:
        merged.append(current)
    return merged


def normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r", "\n")
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    lines = reflow_broken_lines(lines)
    return "\n".join(lines).strip()


def split_long_text(text: str, limit: int = 2400) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        units = re.split(r"(?<=[。！？；?!;])", paragraph) if len(paragraph) > limit else [paragraph]
        for unit in units:
            unit = unit.strip()
            if not unit:
                continue
            pieces = [unit[i : i + limit] for i in range(0, len(unit), limit)] if len(unit) > limit else [unit]
            for piece in pieces:
                if len(current) + len(piece) + 1 <= limit:
                    current = f"{current}\n{piece}".strip()
                else:
                    if current:
                        chunks.append(current)
                    current = piece
        if current and len(current) >= limit:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


def dedupe_chapters(chapters: list[Chapter]) -> list[Chapter]:
    seen: set[tuple[str, str]] = set()
    cleaned: list[Chapter] = []
    for chapter in chapters:
        title = normalize_text(chapter.title)
        text = normalize_text(chapter.text)
        if len(text) < 80:
            continue
        key = (title, text[:200])
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(Chapter(title=title or f"章节{len(cleaned)+1}", text=text))
    return cleaned


def heading_level(text: str) -> int:
    line = normalize_text(text)
    if not line:
        return 0
    if SPECIAL_HEADING_RE.match(line):
        return 1
    if PART_HEADING_RE.match(line):
        return 2
    if CHAPTER_HEADING_RE.match(line):
        return 3
    if SECTION_HEADING_RE.match(line):
        return 4
    return 0


def compose_heading_title(part_title: str, chapter_title: str, section_title: str, fallback: str) -> str:
    pieces = [item for item in [part_title, chapter_title, section_title] if item]
    return " ".join(pieces) if pieces else fallback


def is_heading_suffix_line(text: str) -> bool:
    line = normalize_text(text)
    if not line or is_heading_like_line(line):
        return False
    if len(line) > 30:
        return False
    if re.search(r"[。！？?!；;]$", line):
        return False
    return True


def split_structured_text(title: str, text: str) -> list[Chapter]:
    lines = [line.strip() for line in normalize_text(text).splitlines() if line.strip()]
    clean_title = normalize_text(title)
    if lines and clean_title and lines[0] == clean_title:
        lines = lines[1:]
    if not lines:
        return [Chapter(title=clean_title or "章节", text=normalize_text(text))]

    part_title = clean_title if heading_level(clean_title) == 2 else ""
    chapter_title = clean_title if heading_level(clean_title) == 3 else ""
    section_title = clean_title if heading_level(clean_title) == 4 else ""
    current_title = compose_heading_title(part_title, chapter_title, section_title, clean_title or "章节")
    current_body: list[str] = []
    result: list[Chapter] = []
    saw_structured_heading = False
    expecting_heading_suffix = False

    def flush_current() -> None:
        nonlocal current_body, current_title
        body = normalize_text("\n".join(current_body))
        if body:
            result.append(Chapter(title=current_title or clean_title or "章节", text=body))
        current_body = []

    for line in lines:
        level = heading_level(line)
        if level == 0:
            if expecting_heading_suffix and not current_body and is_heading_suffix_line(line):
                if section_title:
                    section_title = f"{section_title} {line}"
                elif chapter_title:
                    chapter_title = f"{chapter_title} {line}"
                elif part_title:
                    part_title = f"{part_title} {line}"
                current_title = compose_heading_title(part_title, chapter_title, section_title, current_title)
                expecting_heading_suffix = False
                continue
            expecting_heading_suffix = False
            current_body.append(line)
            continue
        saw_structured_heading = True
        flush_current()
        if level == 1:
            part_title = ""
            chapter_title = ""
            section_title = ""
            current_title = line
            expecting_heading_suffix = False
        elif level == 2:
            part_title = line
            chapter_title = ""
            section_title = ""
            current_title = part_title
            expecting_heading_suffix = True
        elif level == 3:
            chapter_title = line
            section_title = ""
            current_title = compose_heading_title(part_title, chapter_title, "", line)
            expecting_heading_suffix = True
        else:
            section_title = line
            current_title = compose_heading_title(part_title, chapter_title, section_title, line)
            expecting_heading_suffix = True
    flush_current()

    if not saw_structured_heading or not result:
        body = normalize_text(text)
        return [Chapter(title=clean_title or "章节", text=body)] if body else []
    return result


def split_structured_chapters(chapters: list[Chapter]) -> list[Chapter]:
    flattened: list[Chapter] = []
    for chapter in chapters:
        flattened.extend(split_structured_text(chapter.title, chapter.text))
    for idx, chapter in enumerate(flattened, start=1):
        chapter.sequence = idx
    return flattened


def build_output_filename(chapter: Chapter) -> str:
    prefix = f"{chapter.sequence:03d}" if chapter.sequence > 0 else "000"
    return f"{prefix}-{sanitize_filename(chapter.title)}.mp3"


def extract_epub_chapters(path: Path) -> list[Chapter]:
    if epub is None or ITEM_DOCUMENT is None:
        raise RuntimeError("当前环境缺少 EPUB 解析依赖 ebooklib。")
    book = epub.read_epub(str(path))
    chapters: list[Chapter] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        text, title = html_fragment_to_text_and_title(item.get_body_content())
        if len(text) < 120:
            continue
        if not title:
            title = text.splitlines()[0]
        if title and text.startswith(title):
            text = text[len(title):].strip()
        if len(text) < 80:
            continue
        chapters.append(Chapter(title=title or f"章节{len(chapters)+1}", text=text))
    return dedupe_chapters(chapters)


def extract_pdf_chapters(path: Path) -> list[Chapter]:
    if PdfReader is None:
        raise RuntimeError("当前环境缺少 PDF 解析依赖 pypdf。")
    reader = PdfReader(str(path))
    text = normalize_text("\n".join(page.extract_text() or "" for page in reader.pages))
    if not text:
        raise ValueError("PDF 未提取到可用文本。")
    pattern = re.compile(r"(?=(第[一二三四五六七八九十百千0-9]+章[^\n]{0,30}))")
    matches = list(pattern.finditer(text))
    if len(matches) < 2:
        return [Chapter(title=path.stem, text=text)]
    chapters: list[Chapter] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        lines = chunk.splitlines()
        title = lines[0][:40] if lines else f"章节{idx+1}"
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else chunk
        chapters.append(Chapter(title=title, text=body or chunk))
    return dedupe_chapters(chapters)


def extract_mobi_chapters(path: Path) -> list[Chapter]:
    if mobi is None:
        raise RuntimeError("当前环境缺少 MOBI 解析依赖 mobi。")
    tempdir, extracted = mobi.extract(str(path))
    extracted_path = Path(extracted)
    try:
        if extracted_path.suffix.lower() == ".epub":
            return extract_epub_chapters(extracted_path)
        if extracted_path.suffix.lower() == ".pdf":
            return extract_pdf_chapters(extracted_path)
        if extracted_path.suffix.lower() in {".html", ".htm"}:
            text, title = html_fragment_to_text_and_title(extracted_path.read_text(encoding="utf-8", errors="ignore"))
            return [Chapter(title=title or path.stem, text=text)]
        raise ValueError("MOBI 解析后得到未知格式。")
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def extract_chapters(path: Path) -> list[Chapter]:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        chapters = extract_epub_chapters(path)
    elif suffix == ".pdf":
        chapters = extract_pdf_chapters(path)
    elif suffix == ".mobi":
        chapters = extract_mobi_chapters(path)
    else:
        raise ValueError(f"不支持的格式: {suffix}")
    return split_structured_chapters(chapters)


async def synthesize_to_mp3(text: str, output_path: Path, voice: str, rate: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        await stream_text_to_file(text, f, voice, rate)


async def stream_text_to_file(
    text: str, file_obj, voice: str, rate: str, progress: Callable[[int], None] | None = None
) -> None:
    chunks = split_long_text(text)
    total_chunks = max(1, len(chunks))
    for idx, chunk in enumerate(chunks, start=1):
        communicate = edge_tts.Communicate(
            text=chunk,
            voice=voice,
            rate=rate,
            pitch="-2Hz",
            volume="+0%",
        )
        async for event in communicate.stream():
            if event["type"] == "audio":
                file_obj.write(event["data"])
        if progress:
            progress(int(idx * 100 / total_chunks))


async def stream_text_to_http(text: str, handler: BaseHTTPRequestHandler, voice: str, rate: str) -> None:
    chunks = split_long_text(text)
    for chunk in chunks:
        communicate = edge_tts.Communicate(
            text=chunk,
            voice=voice,
            rate=rate,
            pitch="-2Hz",
            volume="+0%",
        )
        async for event in communicate.stream():
            if event["type"] == "audio":
                handler.wfile.write(event["data"])
                handler.wfile.flush()


async def synthesize_chapters_to_single_mp3(
    chapters: list[Chapter],
    output_path: Path,
    voice: str,
    rate: str,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        for idx, chapter in enumerate(chapters, start=1):
            if log:
                log(f"[{idx}/{len(chapters)}] 合并写入：{chapter.title}")
            if progress:
                progress(chapter.sequence, 0, "processing")
            await stream_text_to_file(
                chapter.text,
                f,
                voice,
                rate,
                None if progress is None else lambda percent, seq=chapter.sequence: progress(seq, percent, "processing"),
            )
            if progress:
                progress(chapter.sequence, 100, "done")


async def synthesize_chapters_to_files(
    chapters: list[Chapter],
    output_dir: Path,
    voice: str,
    rate: str,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    max_parallel: int = MAX_PARALLEL_CHAPTERS,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(max_parallel)
    total = len(chapters)
    failures: list[str] = []

    async def one_chapter(idx: int, chapter: Chapter) -> None:
        async with semaphore:
            try:
                if log:
                    log(f"[{idx}/{total}] 生成中：{chapter.title}")
                output_path = output_dir / build_output_filename(chapter)
                if progress:
                    progress(chapter.sequence, 0, "processing")
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("wb") as f:
                    await stream_text_to_file(
                        chapter.text,
                        f,
                        voice,
                        rate,
                        None
                        if progress is None
                        else lambda percent, seq=chapter.sequence: progress(seq, percent, "processing"),
                    )
                if progress:
                    progress(chapter.sequence, 100, "done")
                if log:
                    log(f"完成：{output_path}")
            except Exception as exc:
                failures.append(f"{chapter.title}: {exc}")
                if progress:
                    progress(chapter.sequence, 0, "error")
                if log:
                    log(f"失败：{chapter.title}: {exc}")

    await asyncio.gather(*(one_chapter(idx, chapter) for idx, chapter in enumerate(chapters, start=1)))
    if failures:
        raise RuntimeError("；".join(failures))


def run_powershell_dialog(script: str) -> str:
    candidates = ["pwsh", "powershell"]
    errors: list[str] = []
    for cmd in candidates:
        try:
            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                [cmd, "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                timeout=120,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            errors.append(f"{cmd} not found")
            continue
        if result.returncode == 0:
            return result.stdout.strip()
        errors.append(result.stderr.strip() or f"{cmd} exited with {result.returncode}")
    raise RuntimeError("; ".join(errors) or "无法打开系统对话框。")


def pick_file_dialog() -> str:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dlg = New-Object System.Windows.Forms.OpenFileDialog;"
        "$dlg.Filter = 'Books (*.epub;*.mobi;*.pdf)|*.epub;*.mobi;*.pdf|All files (*.*)|*.*';"
        "$dlg.Multiselect = $false;"
        "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dlg.FileName }"
    )
    return run_powershell_dialog(script)


def pick_folder_dialog() -> str:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dlg = New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dlg.ShowNewFolderButton = $true;"
        "if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $dlg.SelectedPath }"
    )
    return run_powershell_dialog(script)


class AppState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.file_path = ""
        self.output_dir = str(DEFAULT_OUTPUT_DIR)
        self.chapters: list[Chapter] = []
        self.logs: list[str] = ["应用已启动。"]
        self.busy = False
        self.status_text = "请选择图书文件，然后点击“解析章节”。"
        self.chapter_progress: dict[str, dict[str, Any]] = {}
        self.preview_cache: dict[str, dict[str, Any]] = {}

    def log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)
            self.logs = self.logs[-200:]

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "file_path": self.file_path,
                "output_dir": self.output_dir,
                "busy": self.busy,
                "status_text": self.status_text,
                "logs": list(self.logs),
                "chapter_progress": dict(self.chapter_progress),
                "preview_cache": dict(self.preview_cache),
            }

    def set_active_chapters(self, chapters: list[Chapter]) -> None:
        with self.lock:
            previous = dict(self.chapter_progress)
            self.chapter_progress = {
                str(chapter.sequence): {
                    "percent": previous.get(str(chapter.sequence), {}).get("percent", 0),
                    "status": previous.get(str(chapter.sequence), {}).get("status", "queued"),
                    "title": chapter.title,
                }
                for chapter in chapters
            }

    def reset_chapter_progress(self) -> None:
        with self.lock:
            self.chapter_progress = {}

    def update_chapter_progress(self, sequence: int, percent: int, status: str) -> None:
        with self.lock:
            key = str(sequence)
            current = self.chapter_progress.get(key, {"percent": 0, "status": "idle"})
            current["percent"] = max(0, min(100, percent))
            current["status"] = status
            self.chapter_progress[key] = current

    def mark_unfinished_chapters_error(self, chapters: list[Chapter]) -> None:
        with self.lock:
            for chapter in chapters:
                key = str(chapter.sequence)
                current = self.chapter_progress.get(key, {"percent": 0, "status": "idle"})
                if current.get("status") != "done":
                    current["status"] = "error"
                    self.chapter_progress[key] = current

    def set_preview_cache(self, preview_cache: dict[str, dict[str, Any]]) -> None:
        with self.lock:
            self.preview_cache = preview_cache

    def update_preview_entry(self, sequence: int, entry: dict[str, Any]) -> None:
        with self.lock:
            self.preview_cache[str(sequence)] = entry

    def clear_preview_entry(self, sequence: int) -> None:
        with self.lock:
            self.preview_cache.pop(str(sequence), None)


def build_single_output_name(file_path: str, selected: list[Chapter]) -> str:
    stem = sanitize_filename(Path(file_path).stem or "book")
    if len(selected) == 1:
        return f"{stem}-{build_output_filename(selected[0])}"
    start = min(chapter.sequence for chapter in selected)
    end = max(chapter.sequence for chapter in selected)
    return f"{stem}-{start:03d}-{end:03d}-合并音频.mp3"


def preview_book_cache_dir(file_path: str) -> Path:
    digest = hashlib.sha1(file_path.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return PREVIEW_CACHE_ROOT / digest


def chapter_preview_paths(cache_dir: Path, chapter: Chapter) -> tuple[Path, Path]:
    base_name = f"{chapter.sequence:03d}-{sanitize_filename(chapter.title)}-preview"
    return cache_dir / f"{base_name}.mp3", cache_dir / f"{base_name}.json"


def preview_cache_entry(cache_dir: Path, chapter: Chapter) -> dict[str, Any]:
    audio_path, meta_path = chapter_preview_paths(cache_dir, chapter)
    voice = ""
    rate = ""
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            voice = meta.get("voice", "") if isinstance(meta.get("voice", ""), str) else ""
            rate = meta.get("rate", "") if isinstance(meta.get("rate", ""), str) else ""
        except Exception:
            voice = ""
            rate = ""
    return {
        "sequence": chapter.sequence,
        "title": chapter.title,
        "exists": audio_path.exists(),
        "voice": voice,
        "rate": rate,
    }


def scan_preview_cache(file_path: str, chapters: list[Chapter]) -> dict[str, dict[str, Any]]:
    if not file_path:
        return {}
    cache_dir = preview_book_cache_dir(file_path)
    preview_cache: dict[str, dict[str, Any]] = {}
    for chapter in chapters:
        entry = preview_cache_entry(cache_dir, chapter)
        if entry["exists"]:
            preview_cache[str(chapter.sequence)] = entry
    return preview_cache


def normalize_generate_request(data: dict[str, Any], chapters: list[Chapter]) -> dict[str, Any]:
    indices = data.get("indices", [])
    if not isinstance(indices, list):
        raise ValueError("章节索引无效。")
    if not chapters:
        raise ValueError("请先解析章节。")

    selected: list[Chapter] = []
    seen: set[int] = set()
    for item in indices:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            raise ValueError("章节索引无效。") from None
        if idx < 0 or idx >= len(chapters):
            raise ValueError("章节索引无效。")
        if idx not in seen:
            selected.append(chapters[idx])
            seen.add(idx)
    if not selected:
        raise ValueError("请至少选择一个章节。")

    output_dir_raw = data.get("output_dir", "")
    if not isinstance(output_dir_raw, str) or not output_dir_raw.strip():
        raise ValueError("输出目录不能为空。")
    output_dir = Path(output_dir_raw.strip()).expanduser()

    voice_raw = data.get("voice", DEFAULT_VOICE)
    voice = voice_raw if isinstance(voice_raw, str) and voice_raw in VALID_VOICE_IDS else DEFAULT_VOICE

    rate_raw = data.get("rate", "-12%")
    rate = rate_raw if isinstance(rate_raw, str) and rate_raw in VALID_RATES else "-12%"

    output_mode_raw = data.get("output_mode", "per_chapter")
    output_mode = output_mode_raw if isinstance(output_mode_raw, str) and output_mode_raw in VALID_OUTPUT_MODES else "per_chapter"

    return {
        "selected": selected,
        "output_dir": output_dir,
        "voice": voice,
        "rate": rate,
        "output_mode": output_mode,
    }


def normalize_preview_request(data: dict[str, Any], chapters: list[Chapter]) -> dict[str, Any]:
    if not chapters:
        raise ValueError("请先解析章节。")
    try:
        idx = int(data.get("index", -1))
    except (TypeError, ValueError):
        raise ValueError("章节索引无效。") from None
    if idx < 0 or idx >= len(chapters):
        raise ValueError("章节索引无效。")

    voice_raw = data.get("voice", DEFAULT_VOICE)
    voice = voice_raw if isinstance(voice_raw, str) and voice_raw in VALID_VOICE_IDS else DEFAULT_VOICE

    rate_raw = data.get("rate", "-12%")
    rate = rate_raw if isinstance(rate_raw, str) and rate_raw in VALID_RATES else "-12%"

    return {
        "index": idx,
        "chapter": chapters[idx],
        "voice": voice,
        "rate": rate,
        "force": bool(data.get("force", False)),
    }


def normalize_instant_read_request(data: dict[str, Any], chapters: list[Chapter]) -> dict[str, Any]:
    normalized = normalize_preview_request(data, chapters)
    return {
        "index": normalized["index"],
        "chapter": normalized["chapter"],
        "voice": normalized["voice"],
        "rate": normalized["rate"],
    }


def build_instant_audio_url(index: int, voice: str, rate: str) -> str:
    return f"/api/instant-audio?{urlencode({'index': index, 'voice': voice, 'rate': rate})}"


def ensure_chapter_preview(
    cache_dir: Path,
    chapter: Chapter,
    voice: str,
    rate: str,
    force: bool = False,
    synthesize: Callable[[str, Path, str, str], Any] = synthesize_to_mp3,
) -> dict[str, Any]:
    current = preview_cache_entry(cache_dir, chapter)
    if current["exists"] and current["voice"] == voice and current["rate"] == rate and not force:
        return current

    audio_path, meta_path = chapter_preview_paths(cache_dir, chapter)
    cache_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(synthesize(chapter.text, audio_path, voice, rate))
    meta_path.write_text(
        json.dumps({"voice": voice, "rate": rate, "title": chapter.title}, ensure_ascii=False),
        encoding="utf-8",
    )
    return preview_cache_entry(cache_dir, chapter)


def clear_chapter_preview(cache_dir: Path, chapter: Chapter) -> bool:
    audio_path, meta_path = chapter_preview_paths(cache_dir, chapter)
    removed = False
    for path in [audio_path, meta_path]:
        if path.exists():
            path.unlink()
            removed = True
    return removed


STATE = AppState()


class BookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(HTML_PAGE)
            return
        if parsed.path == "/api/status":
            self._send_json({"ok": True, **STATE.snapshot()})
            return
        if parsed.path == "/api/preview-audio":
            self._handle_preview_audio(parsed)
            return
        if parsed.path == "/api/instant-audio":
            self._handle_instant_audio(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/pick-file":
            self._handle_pick_file()
            return
        if parsed.path == "/api/pick-folder":
            self._handle_pick_folder()
            return
        if parsed.path == "/api/load-book":
            self._handle_load_book()
            return
        if parsed.path == "/api/generate":
            self._handle_generate()
            return
        if parsed.path == "/api/preview-chapter":
            self._handle_preview_chapter()
            return
        if parsed.path == "/api/preview-clear":
            self._handle_preview_clear()
            return
        if parsed.path == "/api/instant-read":
            self._handle_instant_read()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _send_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        size = path.stat().st_size
        range_header = self.headers.get("Range", "")
        start = 0
        end = size - 1
        status = HTTPStatus.OK
        range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if range_match:
            if range_match.group(1):
                start = int(range_match.group(1))
            if range_match.group(2):
                end = int(range_match.group(2))
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _handle_pick_file(self) -> None:
        try:
            path = pick_file_dialog()
            if path:
                with STATE.lock:
                    STATE.file_path = path
                    STATE.status_text = "已选择图书文件。"
            self._send_json({"ok": True, "path": path})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"文件选择失败：{exc}"}, 500)

    def _handle_pick_folder(self) -> None:
        try:
            path = pick_folder_dialog()
            if path:
                with STATE.lock:
                    STATE.output_dir = path
                    STATE.status_text = "已选择输出目录。"
            self._send_json({"ok": True, "path": path})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"目录选择失败：{exc}"}, 500)

    def _handle_load_book(self) -> None:
        try:
            data = self._read_json()
            path = Path(data.get("path", "")).expanduser()
            if not path.exists():
                raise ValueError("图书文件不存在。")
            if path.suffix.lower() not in SUPPORTED_EXTS:
                raise ValueError("目前只支持 epub / mobi / pdf。")
            chapters = extract_chapters(path)
            if not chapters:
                raise ValueError("没有解析到可朗读内容。")
            with STATE.lock:
                STATE.file_path = str(path)
                STATE.chapters = chapters
                STATE.status_text = f"已解析 {len(chapters)} 个章节。"
                STATE.logs.append(f"已解析文件：{path}")
                STATE.logs.append(f"章节数：{len(chapters)}")
                STATE.reset_chapter_progress()
                STATE.set_preview_cache(scan_preview_cache(str(path), chapters))
            payload = [
                {
                    "title": chapter.title,
                    "sequence": chapter.sequence,
                }
                for chapter in chapters
            ]
            snapshot = STATE.snapshot()
            self._send_json(
                {"ok": True, "chapters": payload, "logs": snapshot["logs"], "preview_cache": snapshot["preview_cache"]}
            )
        except Exception as exc:
            self._send_json({"ok": False, "error": f"解析失败：{exc}"}, 500)

    def _handle_generate(self) -> None:
        try:
            data = self._read_json()
            with STATE.lock:
                if STATE.busy:
                    raise ValueError("已有任务在生成中，请稍后。")
                chapters = STATE.chapters
                normalized = normalize_generate_request(data, chapters)
                selected = normalized["selected"]
                output_dir = normalized["output_dir"]
                voice = normalized["voice"]
                rate = normalized["rate"]
                output_mode = normalized["output_mode"]
                STATE.busy = True
                STATE.output_dir = str(output_dir)
                STATE.status_text = "正在生成音频……"
                STATE.logs.append(f"开始生成，共 {len(selected)} 个章节。")
                STATE.set_active_chapters(selected)

            def worker() -> None:
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    total = len(selected)
                    if output_mode == "single_file":
                        output_path = output_dir / build_single_output_name(STATE.file_path, selected)
                        STATE.log(f"开始合并输出：{output_path}")
                        asyncio.run(
                            synthesize_chapters_to_single_mp3(
                                selected, output_path, voice, rate, STATE.log, STATE.update_chapter_progress
                            )
                        )
                        STATE.log(f"完成：{output_path}")
                    else:
                        STATE.log(f"按章并发生成，最大并发数：{MAX_PARALLEL_CHAPTERS}")
                        asyncio.run(
                            synthesize_chapters_to_files(
                                selected, output_dir, voice, rate, STATE.log, STATE.update_chapter_progress
                            )
                        )
                    with STATE.lock:
                        if output_mode == "single_file":
                            STATE.status_text = "生成完成，已输出 1 个合并 mp3。"
                        else:
                            STATE.status_text = f"生成完成，共 {total} 个 mp3。"
                except Exception as exc:
                    STATE.log(f"失败：{exc}")
                    STATE.mark_unfinished_chapters_error(selected)
                    with STATE.lock:
                        STATE.status_text = f"生成失败：{exc}"
                finally:
                    with STATE.lock:
                        STATE.busy = False

            threading.Thread(target=worker, daemon=True).start()
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"启动失败：{exc}"}, 500)

    def _handle_preview_chapter(self) -> None:
        try:
            data = self._read_json()
            with STATE.lock:
                chapters = list(STATE.chapters)
                file_path = STATE.file_path
            if not file_path:
                raise ValueError("请先解析章节。")
            normalized = normalize_preview_request(data, chapters)
            cache_dir = preview_book_cache_dir(file_path)
            previous = preview_cache_entry(cache_dir, normalized["chapter"])
            entry = ensure_chapter_preview(
                cache_dir,
                normalized["chapter"],
                normalized["voice"],
                normalized["rate"],
                normalized["force"],
            )
            STATE.update_preview_entry(normalized["chapter"].sequence, entry)
            with STATE.lock:
                STATE.status_text = f"试听音频已就绪：{normalized['chapter'].title}"
            reused = (
                previous["exists"]
                and not normalized["force"]
                and previous["voice"] == normalized["voice"]
                and previous["rate"] == normalized["rate"]
            )
            self._send_json({"ok": True, "sequence": normalized["chapter"].sequence, "preview_cache": entry, "reused": reused})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"试听生成失败：{exc}"}, 500)

    def _handle_preview_clear(self) -> None:
        try:
            data = self._read_json()
            with STATE.lock:
                chapters = list(STATE.chapters)
                file_path = STATE.file_path
            if not file_path:
                raise ValueError("请先解析章节。")
            normalized = normalize_preview_request(data, chapters)
            cache_dir = preview_book_cache_dir(file_path)
            removed = clear_chapter_preview(cache_dir, normalized["chapter"])
            STATE.clear_preview_entry(normalized["chapter"].sequence)
            with STATE.lock:
                STATE.status_text = f"已清理试听缓存：{normalized['chapter'].title}"
            self._send_json({"ok": True, "sequence": normalized["chapter"].sequence, "removed": removed})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"试听缓存清理失败：{exc}"}, 500)

    def _handle_preview_audio(self, parsed) -> None:
        try:
            query = parse_qs(parsed.query)
            index_value = query.get("index", ["-1"])[0]
            with STATE.lock:
                chapters = list(STATE.chapters)
                file_path = STATE.file_path
            if not file_path:
                raise ValueError("请先解析章节。")
            normalized = normalize_preview_request({"index": index_value}, chapters)
            cache_dir = preview_book_cache_dir(file_path)
            audio_path, _ = chapter_preview_paths(cache_dir, normalized["chapter"])
            if not audio_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._send_file(audio_path, "audio/mpeg")
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)

    def _handle_instant_read(self) -> None:
        try:
            data = self._read_json()
            with STATE.lock:
                chapters = list(STATE.chapters)
            normalized = normalize_instant_read_request(data, chapters)
            url = build_instant_audio_url(normalized["index"], normalized["voice"], normalized["rate"])
            self._send_json({"ok": True, "sequence": normalized["chapter"].sequence, "url": url})
        except Exception as exc:
            self._send_json({"ok": False, "error": f"即时朗读启动失败：{exc}"}, 500)

    def _handle_instant_audio(self, parsed) -> None:
        try:
            query = parse_qs(parsed.query)
            payload = {
                "index": query.get("index", ["-1"])[0],
                "voice": query.get("voice", [DEFAULT_VOICE])[0],
                "rate": query.get("rate", ["-12%"])[0],
            }
            with STATE.lock:
                chapters = list(STATE.chapters)
            normalized = normalize_instant_read_request(payload, chapters)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            asyncio.run(stream_text_to_http(normalized["chapter"].text, self, normalized["voice"], normalized["rate"]))
        except ValueError:
            self.send_error(HTTPStatus.BAD_REQUEST)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), BookHandler)
    url = f"http://127.0.0.1:{port}/"
    url_file = os.environ.get("BOOK_TO_AUDIO_URL_FILE")
    if url_file:
        Path(url_file).write_text(url, encoding="utf-8")
    print(f"Book To Audio running at {url}", flush=True)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    if os.environ.get("BOOK_TO_AUDIO_NO_BROWSER") == "1":
        server_thread.join()
        return

    window = webview.create_window(
        "Book To Audio",
        url,
        width=1260,
        height=900,
        min_size=(980, 720),
        text_select=True,
    )

    def shutdown_app() -> None:
        try:
            server.shutdown()
            server.server_close()
        finally:
            os._exit(0)

    window.events.closed += shutdown_app
    webview.start(debug=False, http_server=False)


if __name__ == "__main__":
    main()
