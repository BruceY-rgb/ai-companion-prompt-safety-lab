#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from common import ROOT, read_csv, read_jsonl, write_csv, write_jsonl


SAMPLE_REVIEW_FIELDS = ["record_id", "decision", "note", "updated_at"]
RESULT_REVIEW_FIELDS = ["run_id", "manual_label", "note", "updated_at"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_review_csv(path: str, key: str) -> dict[str, dict[str, str]]:
    rows = read_csv(path)
    return {row[key]: row for row in rows if row.get(key)}


def upsert_csv(path: str, fieldnames: list[str], key: str, row: dict[str, str]) -> None:
    rows = read_csv(path)
    replaced = False
    out: list[dict[str, str]] = []
    for existing in rows:
        if existing.get(key) == row.get(key):
            merged = {**existing, **row}
            out.append(merged)
            replaced = True
        else:
            out.append(existing)
    if not replaced:
        out.append(row)
    write_csv(path, out, fieldnames)


def upsert_jsonl(path: str, key: str, row: dict[str, Any]) -> None:
    rows = read_jsonl(path)
    replaced = False
    out: list[dict[str, Any]] = []
    for existing in rows:
        if existing.get(key) == row.get(key):
            out.append({**existing, **row})
            replaced = True
        else:
            out.append(existing)
    if not replaced:
        out.append(row)
    write_jsonl(path, out)


def safe_text(value: Any) -> str:
    return str(value or "")


def read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("request body must be JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def project_file(path: str) -> Path:
    requested = (ROOT / path).resolve()
    if not requested.is_relative_to(ROOT.resolve()):
        raise ValueError("path escapes project root")
    return requested


def candidate_source_path() -> str:
    if (ROOT / "data/raw_posts_with_comments.jsonl").exists():
        return "data/raw_posts_with_comments.jsonl"
    return "data/raw_posts.jsonl"


def api_samples() -> dict[str, Any]:
    source = candidate_source_path()
    rows = read_jsonl(source)
    reviews = load_review_csv("data/manual_sample_review.csv", "record_id")
    enriched: list[dict[str, Any]] = []
    for row in rows:
        record_id = safe_text(row.get("id"))
        review = reviews.get(record_id, {})
        enriched.append(
            {
                "record_id": record_id,
                "post_id": row.get("post_id") or row.get("id"),
                "comment_id": row.get("comment_id") or "",
                "source": row.get("source") or "",
                "subreddit": row.get("subreddit") or "",
                "title": row.get("title") or "",
                "reddit_url": row.get("reddit_url") or "",
                "selftext": row.get("selftext") or "",
                "created_utc": row.get("created_utc") or "",
                "score": row.get("score") or "",
                "over_18": bool(row.get("over_18")),
                "intro_score": row.get("intro_score") or 0,
                "intro_reasons": row.get("intro_reasons") or [],
                "heuristic_intro_candidate": bool(row.get("heuristic_intro_candidate")),
                "decision": review.get("decision") or "",
                "note": review.get("note") or "",
                "updated_at": review.get("updated_at") or "",
            }
        )
    return {"source": source, "items": enriched}


def api_prompts() -> dict[str, Any]:
    samples = {row.get("sample_id"): row for row in read_csv("data/filtered_posts.csv")}
    prompts = read_jsonl("data/system_prompts.jsonl")
    reviewed = {row.get("prompt_id"): row for row in read_jsonl("data/system_prompts_reviewed.jsonl")}
    items: list[dict[str, Any]] = []
    for prompt in prompts:
        prompt_id = prompt.get("prompt_id")
        review = reviewed.get(prompt_id, {})
        sample = samples.get(prompt.get("sample_id"), {})
        items.append(
            {
                **prompt,
                "reviewed_system_prompt": review.get("system_prompt") or prompt.get("system_prompt") or "",
                "review_status": review.get("review_status") or "",
                "review_note": review.get("review_note") or "",
                "reviewed_at": review.get("reviewed_at") or "",
                "intro_text": sample.get("intro_text") or "",
                "anonymized_text": sample.get("anonymized_text") or "",
                "title": sample.get("title") or "",
            }
        )
    return {"items": items}


def api_results() -> dict[str, Any]:
    rows = read_jsonl("data/judged_results.jsonl")
    reviews = load_review_csv("data/manual_result_review.csv", "run_id")
    items: list[dict[str, Any]] = []
    for row in rows:
        run_id = safe_text(row.get("run_id"))
        review = reviews.get(run_id, {})
        items.append(
            {
                **row,
                "manual_label": review.get("manual_label") or "",
                "review_note": review.get("note") or "",
                "reviewed_at": review.get("updated_at") or "",
            }
        )
    return {"items": items}


def api_overview() -> dict[str, Any]:
    files = {
        "raw_posts": "data/raw_posts.jsonl",
        "raw_posts_with_comments": "data/raw_posts_with_comments.jsonl",
        "filtered_posts": "data/filtered_posts.csv",
        "system_prompts": "data/system_prompts.jsonl",
        "judged_results": "data/judged_results.jsonl",
        "sample_reviews": "data/manual_sample_review.csv",
        "prompt_reviews": "data/system_prompts_reviewed.jsonl",
        "result_reviews": "data/manual_result_review.csv",
    }
    counts: dict[str, int] = {}
    for name, path in files.items():
        p = ROOT / path
        if not p.exists():
            counts[name] = 0
        elif p.suffix == ".csv":
            counts[name] = len(read_csv(path))
        else:
            counts[name] = len(read_jsonl(path))
    summary_path = ROOT / "report/summary.md"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    figures = []
    for path in sorted((ROOT / "report/figures").glob("*.svg")):
        figures.append(f"/files/{path.relative_to(ROOT).as_posix()}")
    return {"counts": counts, "summary": summary, "figures": figures}


def save_sample_review(payload: dict[str, Any]) -> dict[str, Any]:
    record_id = safe_text(payload.get("record_id")).strip()
    decision = safe_text(payload.get("decision")).strip()
    note = safe_text(payload.get("note")).strip()
    if not record_id:
        raise ValueError("record_id is required")
    if decision not in {"keep", "reject", "uncertain", ""}:
        raise ValueError("decision must be keep, reject, uncertain, or empty")
    row = {"record_id": record_id, "decision": decision, "note": note, "updated_at": utc_now()}
    upsert_csv("data/manual_sample_review.csv", SAMPLE_REVIEW_FIELDS, "record_id", row)
    return {"ok": True, "row": row}


def save_prompt_review(payload: dict[str, Any]) -> dict[str, Any]:
    prompt_id = safe_text(payload.get("prompt_id")).strip()
    system_prompt = safe_text(payload.get("system_prompt")).strip()
    review_status = safe_text(payload.get("review_status")).strip()
    review_note = safe_text(payload.get("review_note")).strip()
    if not prompt_id:
        raise ValueError("prompt_id is required")
    if not system_prompt:
        raise ValueError("system_prompt is required")
    if review_status not in {"approved", "edited", "needs_work", ""}:
        raise ValueError("review_status must be approved, edited, needs_work, or empty")

    original = next((row for row in read_jsonl("data/system_prompts.jsonl") if row.get("prompt_id") == prompt_id), {})
    row = {
        **original,
        "prompt_id": prompt_id,
        "system_prompt": system_prompt,
        "review_status": review_status,
        "review_note": review_note,
        "reviewed_at": utc_now(),
    }
    upsert_jsonl("data/system_prompts_reviewed.jsonl", "prompt_id", row)
    return {"ok": True, "row": row}


def save_result_review(payload: dict[str, Any]) -> dict[str, Any]:
    run_id = safe_text(payload.get("run_id")).strip()
    manual_label = safe_text(payload.get("manual_label")).strip()
    note = safe_text(payload.get("note")).strip()
    if not run_id:
        raise ValueError("run_id is required")
    if manual_label not in {"safe_refusal", "unsafe_compliance", "partial_compliance", "needs_review", "empty", ""}:
        raise ValueError("unsupported manual_label")
    row = {"run_id": run_id, "manual_label": manual_label, "note": note, "updated_at": utc_now()}
    upsert_csv("data/manual_result_review.csv", RESULT_REVIEW_FIELDS, "run_id", row)
    return {"ok": True, "row": row}


APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Companion Prompt Review Bench</title>
  <link rel="stylesheet" href="/app.css">
</head>
<body>
  <div class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">OSN Lab2</p>
        <h1>Companion Prompt Review Bench</h1>
      </div>
      <div class="status" id="status">Loading</div>
    </header>

    <nav class="tabs" aria-label="Review sections">
      <button class="tab active" data-view="samples">Samples</button>
      <button class="tab" data-view="prompts">Prompts</button>
      <button class="tab" data-view="results">Results</button>
      <button class="tab" data-view="overview">Overview</button>
    </nav>

    <main>
      <section class="view active" id="view-samples"></section>
      <section class="view" id="view-prompts"></section>
      <section class="view" id="view-results"></section>
      <section class="view" id="view-overview"></section>
    </main>
  </div>
  <script src="/app.js"></script>
</body>
</html>
"""


APP_CSS = r""":root {
  color-scheme: light;
  --paper: #f7f4ee;
  --ink: #19201d;
  --muted: #66716a;
  --line: #d8d1c5;
  --line-strong: #b9ae9e;
  --panel: #fffdf8;
  --field: #fbf8f0;
  --teal: #1f6f68;
  --teal-ink: #0f3e39;
  --rust: #a7532c;
  --amber: #cf8a2c;
  --red: #9e3434;
  --violet: #5d557d;
  --shadow: 0 18px 40px rgba(42, 33, 20, 0.12);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  min-height: 100vh;
  background:
    linear-gradient(90deg, rgba(31, 111, 104, .08), transparent 34%),
    linear-gradient(180deg, var(--paper), #efe8db);
  color: var(--ink);
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button, input, select, textarea {
  font: inherit;
}

button {
  border: 0;
  cursor: pointer;
}

a {
  color: var(--teal-ink);
  text-decoration-thickness: 1px;
  text-underline-offset: 3px;
}

.shell {
  width: min(1500px, calc(100vw - 32px));
  margin: 0 auto;
  padding: 22px 0 30px;
}

.topbar {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 18px;
  border-bottom: 1px solid var(--line-strong);
  padding-bottom: 18px;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--rust);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-family: Georgia, "Times New Roman", serif;
  font-size: 34px;
  font-weight: 700;
  letter-spacing: 0;
}

.status {
  min-width: 180px;
  max-width: 360px;
  border-left: 3px solid var(--teal);
  padding: 8px 0 8px 14px;
  color: var(--muted);
  font-size: 13px;
  text-align: right;
}

.tabs {
  display: flex;
  gap: 6px;
  margin: 18px 0;
  border-bottom: 1px solid var(--line);
  overflow-x: auto;
}

.tab {
  background: transparent;
  color: var(--muted);
  padding: 12px 14px 13px;
  border-bottom: 3px solid transparent;
  font-weight: 750;
}

.tab.active {
  color: var(--ink);
  border-color: var(--teal);
}

.view { display: none; }
.view.active { display: block; }

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin: 0 0 14px;
}

.toolbar-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.toolbar-title h2 {
  margin: 0;
  font-size: 18px;
}

.count {
  color: var(--muted);
  font-size: 13px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search,
.select,
.text-input {
  min-height: 38px;
  border: 1px solid var(--line);
  background: var(--field);
  color: var(--ink);
  padding: 8px 10px;
  border-radius: 4px;
  outline: none;
}

.search:focus,
.select:focus,
.text-input:focus,
textarea:focus {
  border-color: var(--teal);
  box-shadow: 0 0 0 3px rgba(31, 111, 104, .14);
}

.workbench {
  display: grid;
  grid-template-columns: minmax(300px, 390px) minmax(0, 1fr);
  gap: 18px;
  align-items: start;
}

.list {
  height: calc(100vh - 198px);
  min-height: 520px;
  overflow: auto;
  border: 1px solid var(--line);
  background: rgba(255, 253, 248, .62);
}

.row {
  width: 100%;
  text-align: left;
  display: block;
  border-bottom: 1px solid var(--line);
  background: transparent;
  padding: 12px 13px;
}

.row:hover {
  background: rgba(31, 111, 104, .07);
}

.row.active {
  background: var(--panel);
  box-shadow: inset 4px 0 0 var(--teal);
}

.row-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.row-title strong {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}

.row-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  color: var(--muted);
  font-size: 12px;
}

.detail {
  min-height: 520px;
  border: 1px solid var(--line-strong);
  background: var(--panel);
  box-shadow: var(--shadow);
}

.detail-head,
.detail-actions,
.detail-section {
  padding: 16px 18px;
}

.detail-head {
  border-bottom: 1px solid var(--line);
}

.detail-head h2,
.detail-head h3 {
  margin: 0 0 6px;
  font-size: 20px;
  line-height: 1.25;
}

.detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid var(--line);
  background: #fbf7ed;
  flex-wrap: wrap;
}

.button-group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.btn {
  min-height: 36px;
  padding: 8px 12px;
  border-radius: 4px;
  background: #e8ded0;
  color: var(--ink);
  font-weight: 760;
}

.btn:hover { filter: brightness(.97); }
.btn.keep { background: #d5e6dc; color: #16443e; }
.btn.reject { background: #edd8d6; color: #752c2b; }
.btn.warn { background: #efe0bd; color: #704911; }
.btn.primary { background: var(--teal); color: #f8fbf8; }

.pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 3px 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  background: rgba(255, 255, 255, .46);
  font-size: 12px;
}

.pill.keep,
.pill.safe_refusal { border-color: #95b9a9; color: #1c5b51; }
.pill.reject,
.pill.unsafe_compliance { border-color: #c98d86; color: #8a2f2c; }
.pill.uncertain,
.pill.needs_review,
.pill.partial_compliance { border-color: #d4ae65; color: #7a5412; }
.pill.companion { border-color: #9b93bd; color: var(--violet); }

.text {
  margin: 0;
  color: var(--ink);
  line-height: 1.55;
  white-space: pre-wrap;
}

.markdown {
  color: var(--ink);
  line-height: 1.58;
  overflow-wrap: anywhere;
}

.markdown > :first-child { margin-top: 0; }
.markdown > :last-child { margin-bottom: 0; }

.markdown p {
  margin: 0 0 13px;
}

.markdown h1,
.markdown h2,
.markdown h3,
.markdown h4 {
  margin: 18px 0 8px;
  font-family: Georgia, "Times New Roman", serif;
  letter-spacing: 0;
  line-height: 1.22;
}

.markdown h1 { font-size: 24px; }
.markdown h2 { font-size: 21px; }
.markdown h3 { font-size: 18px; }
.markdown h4 { font-size: 16px; }

.markdown ul,
.markdown ol {
  margin: 0 0 14px 22px;
  padding: 0;
}

.markdown li {
  margin: 5px 0;
}

.markdown blockquote {
  margin: 0 0 14px;
  padding: 9px 14px;
  border-left: 4px solid var(--teal);
  background: #f2eadf;
  color: #3f4842;
}

.markdown code {
  background: #eee3d2;
  border: 1px solid #d9cbb9;
  border-radius: 3px;
  padding: 1px 4px;
  font-size: .94em;
}

.markdown pre {
  margin: 0 0 14px;
  padding: 12px;
  border: 1px solid var(--line);
  background: #29251f;
  color: #f8f2e8;
  overflow: auto;
}

.markdown pre code {
  background: transparent;
  border: 0;
  padding: 0;
  color: inherit;
}

.markdown a {
  font-weight: 650;
}

.markdown table {
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 16px;
  font-size: 14px;
  background: #fffaf2;
}

.markdown th,
.markdown td {
  border: 1px solid var(--line);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
}

.markdown th {
  background: #eee3d2;
  color: var(--teal-ink);
  font-weight: 800;
}

.markdown tr:nth-child(even) td {
  background: #f7f0e5;
}

.muted { color: var(--muted); }

.grid-two {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
}

textarea {
  width: 100%;
  min-height: 260px;
  resize: vertical;
  border: 1px solid var(--line);
  background: #fffaf2;
  color: var(--ink);
  padding: 12px;
  border-radius: 4px;
  line-height: 1.5;
  outline: none;
}

.note {
  width: min(520px, 100%);
}

.empty {
  border: 1px dashed var(--line-strong);
  padding: 28px;
  color: var(--muted);
  background: rgba(255, 253, 248, .55);
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.metric {
  border-top: 4px solid var(--teal);
  background: var(--panel);
  padding: 14px;
  box-shadow: var(--shadow);
}

.metric:nth-child(2) { border-color: var(--rust); }
.metric:nth-child(3) { border-color: var(--amber); }
.metric:nth-child(4) { border-color: var(--violet); }

.metric b {
  display: block;
  font-size: 28px;
}

.metric span {
  display: block;
  color: var(--muted);
  font-size: 13px;
}

.summary {
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 18px;
  overflow: auto;
}

.figures {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.figure {
  background: var(--panel);
  border: 1px solid var(--line);
  padding: 12px;
  overflow: auto;
}

.figure img {
  display: block;
  width: 100%;
  height: auto;
}

@media (max-width: 980px) {
  .shell { width: min(100vw - 20px, 1500px); }
  .topbar { align-items: start; flex-direction: column; }
  .status { text-align: left; }
  .toolbar { align-items: stretch; flex-direction: column; }
  .workbench { grid-template-columns: 1fr; }
  .list { height: 360px; min-height: 320px; }
  .grid-two,
  .figures,
  .metrics { grid-template-columns: 1fr; }
}
"""


APP_JS = r"""const state = {
  view: "samples",
  samples: [],
  prompts: [],
  results: [],
  overview: null,
  selected: { samples: 0, prompts: 0, results: 0 },
  scroll: { samples: 0, prompts: 0, results: 0 },
  filters: {
    samples: { q: "", decision: "all" },
    prompts: { q: "", status: "all" },
    results: { q: "", label: "review", condition: "all" }
  }
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function setStatus(text) {
  $("#status").textContent = text;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value) {
  let text = escapeHtml(value);
  const links = [];
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_match, label, url) => {
    const token = `@@LINK_${links.length}@@`;
    links.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label}</a>`);
    return token;
  });
  text = text.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_match, prefix, url) => {
    const token = `@@LINK_${links.length}@@`;
    links.push(`<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a>`);
    return `${prefix}${token}`;
  });
  text = text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  links.forEach((html, index) => {
    text = text.replaceAll(`@@LINK_${index}@@`, html);
  });
  return text;
}

function parseMarkdownTableLine(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith("|") || !trimmed.endsWith("|")) return null;
  return trimmed.slice(1, -1).split("|").map((cell) => cell.trim());
}

function isMarkdownTableDivider(line) {
  const cells = parseMarkdownTableLine(line);
  if (!cells || !cells.length) return false;
  return cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdown(value) {
  const lines = String(value ?? "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let listType = null;
  let listItems = [];
  let codeLines = [];
  let inCode = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!listType) return;
    html.push(`<${listType}>${listItems.map((item) => `<li>${inlineMarkdown(item)}</li>`).join("")}</${listType}>`);
    listType = null;
    listItems = [];
  };
  const closeBlocks = () => {
    flushParagraph();
    flushList();
  };

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const rawLine = lines[lineIndex];
    const line = rawLine.replace(/\s+$/, "");
    if (/^\s*```/.test(line)) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeBlocks();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(rawLine);
      continue;
    }
    if (!line.trim()) {
      closeBlocks();
      continue;
    }

    const tableHeader = parseMarkdownTableLine(line);
    const nextLine = lines[lineIndex + 1] ?? "";
    if (tableHeader && isMarkdownTableDivider(nextLine)) {
      closeBlocks();
      const bodyRows = [];
      lineIndex += 2;
      while (lineIndex < lines.length) {
        const row = parseMarkdownTableLine(lines[lineIndex]);
        if (!row) {
          lineIndex -= 1;
          break;
        }
        bodyRows.push(row);
        lineIndex += 1;
      }
      const headerHtml = tableHeader.map((cell) => `<th>${inlineMarkdown(cell)}</th>`).join("");
      const bodyHtml = bodyRows.map((row) => {
        const cells = tableHeader.map((_header, index) => `<td>${inlineMarkdown(row[index] ?? "")}</td>`).join("");
        return `<tr>${cells}</tr>`;
      }).join("");
      html.push(`<table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table>`);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeBlocks();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      closeBlocks();
      html.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    if (unordered) {
      flushParagraph();
      if (listType !== "ul") flushList();
      listType = "ul";
      listItems.push(unordered[1]);
      continue;
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      if (listType !== "ol") flushList();
      listType = "ol";
      listItems.push(ordered[1]);
      continue;
    }

    flushList();
    paragraph.push(line);
  }

  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  closeBlocks();
  return html.join("");
}

function textMatches(item, query, fields) {
  if (!query) return true;
  const q = query.toLowerCase();
  return fields.some((field) => String(item[field] ?? "").toLowerCase().includes(q));
}

function pill(value, fallback = "unmarked") {
  const label = value || fallback;
  return `<span class="pill ${escapeHtml(label)}">${escapeHtml(label)}</span>`;
}

function itemLabel(item) {
  return item.sample_id || item.record_id || item.prompt_id || item.run_id || "item";
}

function bindTabs() {
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      $$(".tab").forEach((b) => b.classList.toggle("active", b === button));
      $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.view}`));
      render();
    });
  });
}

async function loadAll() {
  setStatus("Loading data");
  const [samples, prompts, results, overview] = await Promise.all([
    api("/api/samples"),
    api("/api/prompts"),
    api("/api/results"),
    api("/api/overview")
  ]);
  state.samples = samples.items;
  state.prompts = prompts.items;
  state.results = results.items;
  state.overview = overview;
  setStatus(`Loaded ${state.samples.length} candidates, ${state.prompts.length} prompts, ${state.results.length} results`);
  render();
}

function filteredSamples() {
  const f = state.filters.samples;
  return state.samples.filter((item) => {
    if (f.decision !== "all" && (item.decision || "unmarked") !== f.decision) return false;
    return textMatches(item, f.q, ["record_id", "title", "selftext", "subreddit", "source"]);
  });
}

function filteredPrompts() {
  const f = state.filters.prompts;
  return state.prompts.filter((item) => {
    if (f.status !== "all" && (item.review_status || "unreviewed") !== f.status) return false;
    return textMatches(item, f.q, ["prompt_id", "sample_id", "companion_name", "system_prompt", "reviewed_system_prompt", "intro_text"]);
  });
}

function filteredResults() {
  const f = state.filters.results;
  return state.results.filter((item) => {
    const effectiveLabel = item.manual_label || item.judge_label || "";
    if (f.label === "review" && !item.requires_manual_review && !["unsafe_compliance", "partial_compliance", "needs_review"].includes(effectiveLabel)) return false;
    if (f.label !== "all" && f.label !== "review" && effectiveLabel !== f.label) return false;
    if (f.condition !== "all" && item.condition !== f.condition) return false;
    return textMatches(item, f.q, ["run_id", "prompt_id", "question_id", "category", "prompt_text", "response_text"]);
  });
}

function renderList(items, selectedIndex, onSelect, rowHtml) {
  if (!items.length) return `<div class="empty">No matching records.</div>`;
  return items.map((item, index) => `
    <button class="row ${index === selectedIndex ? "active" : ""}" data-index="${index}" type="button">
      ${rowHtml(item)}
    </button>
  `).join("");
}

function mountList(root, selector, items, section) {
  $$(selector, root).forEach((row) => {
    row.addEventListener("click", () => {
      const list = row.closest(".list");
      if (list) state.scroll[section] = list.scrollTop;
      state.selected[section] = Number(row.dataset.index);
      render();
    });
  });
}

function restoreListScroll(root, section) {
  const list = $(".list", root);
  if (!list) return;
  list.scrollTop = state.scroll[section] || 0;
  list.addEventListener("scroll", () => {
    state.scroll[section] = list.scrollTop;
  }, { passive: true });
}

function renderSamples() {
  const root = $("#view-samples");
  const items = filteredSamples();
  const selected = items[Math.min(state.selected.samples, Math.max(0, items.length - 1))];
  root.innerHTML = `
    <div class="toolbar">
      <div class="toolbar-title"><h2>Candidate Review</h2><span class="count">${items.length} shown / ${state.samples.length} total</span></div>
      <div class="filters">
        <input class="search" id="sample-q" placeholder="Search text, source, subreddit" value="${escapeHtml(state.filters.samples.q)}">
        <select class="select" id="sample-decision">
          ${["all", "unmarked", "keep", "reject", "uncertain"].map(v => `<option value="${v}" ${state.filters.samples.decision === v ? "selected" : ""}>${v}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="workbench">
      <aside class="list">${renderList(items, state.selected.samples, null, (item) => `
        <div class="row-title"><strong>${escapeHtml(item.record_id)}</strong>${pill(item.decision, item.heuristic_intro_candidate ? "candidate" : "weak")}</div>
        <div class="row-meta"><span>score ${escapeHtml(item.intro_score)}</span><span>${escapeHtml(item.subreddit)}</span><span>${escapeHtml(item.source)}</span></div>
      `)}</aside>
      <section class="detail">${selected ? sampleDetail(selected) : `<div class="detail-section empty">Select a candidate.</div>`}</section>
    </div>
  `;
  $("#sample-q").addEventListener("input", (e) => { state.filters.samples.q = e.target.value; state.selected.samples = 0; state.scroll.samples = 0; renderSamples(); });
  $("#sample-decision").addEventListener("change", (e) => { state.filters.samples.decision = e.target.value; state.selected.samples = 0; state.scroll.samples = 0; renderSamples(); });
  mountList(root, ".row", items, "samples");
  restoreListScroll(root, "samples");
  bindSampleActions(root, selected);
}

function sampleDetail(item) {
  return `
    <div class="detail-head">
      <h2>${escapeHtml(item.title || item.record_id)}</h2>
      <div class="row-meta">
        ${pill(item.decision, "unmarked")}
        <span>${escapeHtml(item.subreddit)}</span>
        <span>auto score ${escapeHtml(item.intro_score)}</span>
        <a href="${escapeHtml(item.reddit_url)}" target="_blank" rel="noreferrer">Open Reddit</a>
      </div>
    </div>
    <div class="detail-actions">
      <div class="button-group">
        <button class="btn keep" data-decision="keep">Keep</button>
        <button class="btn reject" data-decision="reject">Reject</button>
        <button class="btn warn" data-decision="uncertain">Uncertain</button>
        <button class="btn" data-decision="">Clear</button>
      </div>
      <input class="text-input note" id="sample-note" placeholder="Reviewer note" value="${escapeHtml(item.note)}">
    </div>
    <div class="detail-section">
      <p class="muted">Reasons: ${escapeHtml((item.intro_reasons || []).join(", ") || "none")}</p>
      <div class="markdown">${renderMarkdown(item.selftext)}</div>
    </div>
  `;
}

function bindSampleActions(root, item) {
  if (!item) return;
  $$("[data-decision]", root).forEach((button) => {
    button.addEventListener("click", async () => {
      const decision = button.dataset.decision;
      const note = $("#sample-note")?.value || "";
      await api("/api/samples/review", { method: "POST", body: JSON.stringify({ record_id: item.record_id, decision, note }) });
      item.decision = decision;
      item.note = note;
      setStatus(`Saved sample ${item.record_id}`);
      renderSamples();
    });
  });
}

function renderPrompts() {
  const root = $("#view-prompts");
  const items = filteredPrompts();
  const selected = items[Math.min(state.selected.prompts, Math.max(0, items.length - 1))];
  root.innerHTML = `
    <div class="toolbar">
      <div class="toolbar-title"><h2>System Prompt Review</h2><span class="count">${items.length} shown / ${state.prompts.length} total</span></div>
      <div class="filters">
        <input class="search" id="prompt-q" placeholder="Search prompt or intro" value="${escapeHtml(state.filters.prompts.q)}">
        <select class="select" id="prompt-status">
          ${["all", "unreviewed", "approved", "edited", "needs_work"].map(v => `<option value="${v}" ${state.filters.prompts.status === v ? "selected" : ""}>${v}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="workbench">
      <aside class="list">${renderList(items, state.selected.prompts, null, (item) => `
        <div class="row-title"><strong>${escapeHtml(item.sample_id || item.prompt_id)}</strong>${pill(item.review_status, "unreviewed")}</div>
        <div class="row-meta"><span>${escapeHtml(item.companion_name || "unknown")}</span><span>${escapeHtml(item.relationship_type || "")}</span></div>
      `)}</aside>
      <section class="detail">${selected ? promptDetail(selected) : `<div class="detail-section empty">Select a prompt.</div>`}</section>
    </div>
  `;
  $("#prompt-q").addEventListener("input", (e) => { state.filters.prompts.q = e.target.value; state.selected.prompts = 0; state.scroll.prompts = 0; renderPrompts(); });
  $("#prompt-status").addEventListener("change", (e) => { state.filters.prompts.status = e.target.value; state.selected.prompts = 0; state.scroll.prompts = 0; renderPrompts(); });
  mountList(root, ".row", items, "prompts");
  restoreListScroll(root, "prompts");
  bindPromptActions(root, selected);
}

function promptDetail(item) {
  return `
    <div class="detail-head">
      <h2>${escapeHtml(item.sample_id)} / ${escapeHtml(item.companion_name || "unknown")}</h2>
      <div class="row-meta">
        ${pill(item.review_status, "unreviewed")}
        <span>${escapeHtml(item.relationship_type || "")}</span>
        <a href="${escapeHtml(item.reddit_url || "#")}" target="_blank" rel="noreferrer">Source</a>
      </div>
    </div>
    <div class="detail-actions">
      <div class="button-group">
        <select class="select" id="prompt-review-status">
          ${["", "approved", "edited", "needs_work"].map(v => `<option value="${v}" ${item.review_status === v ? "selected" : ""}>${v || "status"}</option>`).join("")}
        </select>
        <button class="btn primary" id="save-prompt">Save Prompt Review</button>
      </div>
      <input class="text-input note" id="prompt-note" placeholder="Reviewer note" value="${escapeHtml(item.review_note || "")}">
    </div>
    <div class="detail-section grid-two">
      <div>
        <p class="muted">Anonymized source</p>
        <p class="text">${escapeHtml(item.anonymized_text || item.intro_text || "")}</p>
      </div>
      <div>
        <p class="muted">Editable system prompt</p>
        <textarea id="prompt-editor">${escapeHtml(item.reviewed_system_prompt || item.system_prompt || "")}</textarea>
      </div>
    </div>
  `;
}

function bindPromptActions(root, item) {
  if (!item) return;
  $("#save-prompt", root).addEventListener("click", async () => {
    const system_prompt = $("#prompt-editor").value;
    const review_status = $("#prompt-review-status").value;
    const review_note = $("#prompt-note").value;
    await api("/api/prompts/review", { method: "POST", body: JSON.stringify({ prompt_id: item.prompt_id, system_prompt, review_status, review_note }) });
    item.reviewed_system_prompt = system_prompt;
    item.review_status = review_status;
    item.review_note = review_note;
    setStatus(`Saved prompt ${item.prompt_id}`);
    renderPrompts();
  });
}

function renderResults() {
  const root = $("#view-results");
  const items = filteredResults();
  const selected = items[Math.min(state.selected.results, Math.max(0, items.length - 1))];
  root.innerHTML = `
    <div class="toolbar">
      <div class="toolbar-title"><h2>Safety Result Review</h2><span class="count">${items.length} shown / ${state.results.length} total</span></div>
      <div class="filters">
        <input class="search" id="result-q" placeholder="Search question, response, category" value="${escapeHtml(state.filters.results.q)}">
        <select class="select" id="result-label">
          ${["review", "all", "safe_refusal", "unsafe_compliance", "partial_compliance", "needs_review", "empty"].map(v => `<option value="${v}" ${state.filters.results.label === v ? "selected" : ""}>${v}</option>`).join("")}
        </select>
        <select class="select" id="result-condition">
          ${["all", "control", "companion"].map(v => `<option value="${v}" ${state.filters.results.condition === v ? "selected" : ""}>${v}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="workbench">
      <aside class="list">${renderList(items, state.selected.results, null, (item) => `
        <div class="row-title"><strong>${escapeHtml(item.question_id)} / ${escapeHtml(item.category)}</strong>${pill(item.manual_label || item.judge_label)}</div>
        <div class="row-meta"><span>${escapeHtml(item.condition)}</span><span>${escapeHtml(item.prompt_id)}</span><span>sev ${escapeHtml(item.severity)}</span></div>
      `)}</aside>
      <section class="detail">${selected ? resultDetail(selected) : `<div class="detail-section empty">Select a result.</div>`}</section>
    </div>
  `;
  $("#result-q").addEventListener("input", (e) => { state.filters.results.q = e.target.value; state.selected.results = 0; state.scroll.results = 0; renderResults(); });
  $("#result-label").addEventListener("change", (e) => { state.filters.results.label = e.target.value; state.selected.results = 0; state.scroll.results = 0; renderResults(); });
  $("#result-condition").addEventListener("change", (e) => { state.filters.results.condition = e.target.value; state.selected.results = 0; state.scroll.results = 0; renderResults(); });
  mountList(root, ".row", items, "results");
  restoreListScroll(root, "results");
  bindResultActions(root, selected);
}

function resultDetail(item) {
  return `
    <div class="detail-head">
      <h2>${escapeHtml(item.question_id)} / ${escapeHtml(item.category)}</h2>
      <div class="row-meta">
        ${pill(item.condition)}
        ${pill(item.judge_label)}
        ${item.manual_label ? pill(item.manual_label) : ""}
        <span>${escapeHtml(item.prompt_id)}</span>
      </div>
    </div>
    <div class="detail-actions">
      <div class="button-group">
        <select class="select" id="manual-label">
          ${["", "safe_refusal", "unsafe_compliance", "partial_compliance", "needs_review", "empty"].map(v => `<option value="${v}" ${item.manual_label === v ? "selected" : ""}>${v || "manual label"}</option>`).join("")}
        </select>
        <button class="btn primary" id="save-result">Save Result Review</button>
      </div>
      <input class="text-input note" id="result-note" placeholder="Reviewer note" value="${escapeHtml(item.review_note || "")}">
    </div>
    <div class="detail-section">
      <p class="muted">Safety question</p>
      <p class="text">${escapeHtml(item.prompt_text || "")}</p>
    </div>
    <div class="detail-section">
      <p class="muted">Model response</p>
      <p class="text">${escapeHtml(item.response_text || "")}</p>
    </div>
  `;
}

function bindResultActions(root, item) {
  if (!item) return;
  $("#save-result", root).addEventListener("click", async () => {
    const manual_label = $("#manual-label").value;
    const note = $("#result-note").value;
    await api("/api/results/review", { method: "POST", body: JSON.stringify({ run_id: item.run_id, manual_label, note }) });
    item.manual_label = manual_label;
    item.review_note = note;
    setStatus(`Saved result ${item.run_id}`);
    renderResults();
  });
}

function renderOverview() {
  const root = $("#view-overview");
  const o = state.overview || { counts: {}, summary: "", figures: [] };
  const metricItems = [
    ["Candidates", o.counts.raw_posts_with_comments || o.counts.raw_posts || 0],
    ["Filtered", o.counts.filtered_posts || 0],
    ["Prompts", o.counts.system_prompts || 0],
    ["Results", o.counts.judged_results || 0]
  ];
  root.innerHTML = `
    <div class="metrics">
      ${metricItems.map(([label, value]) => `<div class="metric"><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`).join("")}
    </div>
    <div class="summary markdown">${renderMarkdown(o.summary || "Run analyze_results.py to generate report/summary.md.")}</div>
    <div class="figures">
      ${(o.figures || []).map(src => `<div class="figure"><img alt="Analysis figure" src="${escapeHtml(src)}"></div>`).join("")}
    </div>
  `;
}

function render() {
  if (state.view === "samples") renderSamples();
  if (state.view === "prompts") renderPrompts();
  if (state.view === "results") renderResults();
  if (state.view === "overview") renderOverview();
}

window.addEventListener("error", (event) => setStatus(event.message));
bindTabs();
loadAll().catch((error) => setStatus(error.message));
"""


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "OSNReviewServer/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[review] {self.address_string()} - {fmt % args}")

    def send_text(self, text: str, content_type: str = "text/plain; charset=utf-8", status: HTTPStatus = HTTPStatus.OK) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                self.send_text(APP_HTML, "text/html; charset=utf-8")
            elif path == "/app.css":
                self.send_text(APP_CSS, "text/css; charset=utf-8")
            elif path == "/app.js":
                self.send_text(APP_JS, "application/javascript; charset=utf-8")
            elif path == "/api/samples":
                self.send_json(api_samples())
            elif path == "/api/prompts":
                self.send_json(api_prompts())
            elif path == "/api/results":
                self.send_json(api_results())
            elif path == "/api/overview":
                self.send_json(api_overview())
            elif path.startswith("/files/"):
                self.serve_file(path.removeprefix("/files/"))
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            payload = read_body(self)
            if path == "/api/samples/review":
                self.send_json(save_sample_review(payload))
            elif path == "/api/prompts/review":
                self.send_json(save_prompt_review(payload))
            elif path == "/api/results/review":
                self.send_json(save_result_review(payload))
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_file(self, relative_path: str) -> None:
        path = project_file(unquote(relative_path))
        if not path.exists() or not path.is_file():
            self.send_json({"error": "file not found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local review UI for the OSN Lab2 experiment.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"Review UI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping review UI")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
