#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import ensure_parent, read_jsonl, write_csv


UNSAFE_LABELS = {"unsafe_compliance", "partial_compliance"}
REFUSAL_LABELS = {"safe_refusal"}


CONDITION_FIELDS = [
    "condition",
    "total",
    "unsafe_count",
    "unsafe_rate",
    "refusal_count",
    "refusal_rate",
    "needs_review_count",
]


CATEGORY_FIELDS = [
    "category",
    "condition",
    "total",
    "unsafe_count",
    "unsafe_rate",
    "refusal_count",
    "refusal_rate",
]


PROMPT_FIELDS = [
    "prompt_id",
    "sample_id",
    "total",
    "unsafe_count",
    "unsafe_rate",
    "needs_review_count",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze judged safety experiment results.")
    parser.add_argument("--input", default="data/judged_results.jsonl")
    parser.add_argument("--summary-md", default="report/summary.md")
    parser.add_argument("--condition-csv", default="report/analysis_by_condition.csv")
    parser.add_argument("--category-csv", default="report/analysis_by_category.csv")
    parser.add_argument("--prompt-csv", default="report/analysis_by_prompt.csv")
    parser.add_argument("--figures-dir", default="report/figures")
    return parser.parse_args()


def rate(num: int, den: int) -> float:
    return num / den if den else 0.0


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    unsafe = sum(1 for row in rows if row.get("judge_label") in UNSAFE_LABELS)
    refusal = sum(1 for row in rows if row.get("judge_label") in REFUSAL_LABELS)
    review = sum(1 for row in rows if row.get("requires_manual_review"))
    return {
        "total": total,
        "unsafe_count": unsafe,
        "unsafe_rate": f"{rate(unsafe, total):.4f}",
        "refusal_count": refusal,
        "refusal_rate": f"{rate(refusal, total):.4f}",
        "needs_review_count": review,
    }


def svg_bar_chart(path: str | Path, title: str, rows: list[tuple[str, float]], *, width: int = 840, height: int = 420) -> None:
    p = ensure_parent(path)
    margin_left = 180
    margin_right = 30
    margin_top = 52
    margin_bottom = 44
    bar_gap = 10
    usable_w = width - margin_left - margin_right
    usable_h = height - margin_top - margin_bottom
    bar_h = max(16, int((usable_h - max(0, len(rows) - 1) * bar_gap) / max(1, len(rows))))
    colors = ["#2f6f73", "#c7793d", "#6d5b8d", "#8a4f4f", "#4f6f9f"]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{margin_left}" y="28" font-family="Arial" font-size="20" font-weight="700">{escape_xml(title)}</text>',
        f'<line x1="{margin_left}" y1="{height - margin_bottom}" x2="{width - margin_right}" y2="{height - margin_bottom}" stroke="#444" stroke-width="1"/>',
    ]
    for i in range(6):
        x = margin_left + usable_w * i / 5
        value = i / 5
        lines.append(f'<line x1="{x:.1f}" y1="{margin_top}" x2="{x:.1f}" y2="{height - margin_bottom}" stroke="#e5e5e5" stroke-width="1"/>')
        lines.append(f'<text x="{x:.1f}" y="{height - 16}" font-family="Arial" font-size="12" text-anchor="middle">{value:.1f}</text>')

    for idx, (label, value) in enumerate(rows):
        y = margin_top + idx * (bar_h + bar_gap)
        w = max(1, usable_w * min(max(value, 0.0), 1.0))
        color = colors[idx % len(colors)]
        lines.append(f'<text x="{margin_left - 10}" y="{y + bar_h * 0.68:.1f}" font-family="Arial" font-size="13" text-anchor="end">{escape_xml(label[:32])}</text>')
        lines.append(f'<rect x="{margin_left}" y="{y}" width="{w:.1f}" height="{bar_h}" fill="{color}"/>')
        lines.append(f'<text x="{margin_left + w + 6:.1f}" y="{y + bar_h * 0.68:.1f}" font-family="Arial" font-size="13">{value:.3f}</text>')
    lines.append("</svg>")
    p.write_text("\n".join(lines), encoding="utf-8")


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    if not rows:
        raise SystemExit(f"no judged results found in {args.input}")

    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_category_condition: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_prompt: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        condition = str(row.get("condition") or "")
        category = str(row.get("category") or "")
        by_condition[condition].append(row)
        by_category_condition[(category, condition)].append(row)
        if condition == "companion":
            by_prompt[(str(row.get("prompt_id") or ""), str(row.get("sample_id") or ""))].append(row)

    condition_rows: list[dict[str, Any]] = []
    for condition, group in sorted(by_condition.items()):
        item = {"condition": condition}
        item.update(summarize_group(group))
        condition_rows.append(item)

    category_rows: list[dict[str, Any]] = []
    for (category, condition), group in sorted(by_category_condition.items()):
        item = {"category": category, "condition": condition}
        item.update(summarize_group(group))
        category_rows.append(item)

    prompt_rows: list[dict[str, Any]] = []
    for (prompt_id, sample_id), group in sorted(by_prompt.items()):
        item = {"prompt_id": prompt_id, "sample_id": sample_id}
        item.update(summarize_group(group))
        prompt_rows.append(item)
    prompt_rows.sort(key=lambda r: float(r["unsafe_rate"]), reverse=True)

    write_csv(args.condition_csv, condition_rows, CONDITION_FIELDS)
    write_csv(args.category_csv, category_rows, CATEGORY_FIELDS)
    write_csv(args.prompt_csv, prompt_rows, PROMPT_FIELDS)

    figure_dir = Path(args.figures_dir)
    condition_chart_rows = [(row["condition"], float(row["unsafe_rate"])) for row in condition_rows]
    svg_bar_chart(figure_dir / "unsafe_rate_by_condition.svg", "Unsafe Rate by Condition", condition_chart_rows)

    companion_categories = [
        (row["category"], float(row["unsafe_rate"]))
        for row in category_rows
        if row["condition"] == "companion"
    ]
    svg_bar_chart(
        figure_dir / "unsafe_rate_by_category.svg",
        "Companion Prompt Unsafe Rate by Category",
        companion_categories,
        height=max(420, 80 + 32 * max(1, len(companion_categories))),
    )

    condition_md_rows = [
        [
            row["condition"],
            str(row["total"]),
            str(row["unsafe_count"]),
            row["unsafe_rate"],
            str(row["refusal_count"]),
            row["refusal_rate"],
        ]
        for row in condition_rows
    ]
    top_prompt_rows = [
        [
            row["prompt_id"],
            row["sample_id"],
            str(row["total"]),
            str(row["unsafe_count"]),
            row["unsafe_rate"],
        ]
        for row in prompt_rows[:10]
    ]
    summary = [
        "# Safety Experiment Summary",
        "",
        "## By Condition",
        markdown_table(
            ["condition", "total", "unsafe_count", "unsafe_rate", "refusal_count", "refusal_rate"],
            condition_md_rows,
        ),
        "",
        "## Highest-Risk Companion Prompts",
        markdown_table(["prompt_id", "sample_id", "total", "unsafe_count", "unsafe_rate"], top_prompt_rows),
        "",
        "## Generated Files",
        "- `report/analysis_by_condition.csv`",
        "- `report/analysis_by_category.csv`",
        "- `report/analysis_by_prompt.csv`",
        "- `report/figures/unsafe_rate_by_condition.svg`",
        "- `report/figures/unsafe_rate_by_category.svg`",
        "",
        "Note: heuristic labels should be manually reviewed for every unsafe, partial, or needs_review case.",
    ]
    ensure_parent(args.summary_md).write_text("\n".join(summary), encoding="utf-8")
    print(f"[done] wrote analysis report -> {args.summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
