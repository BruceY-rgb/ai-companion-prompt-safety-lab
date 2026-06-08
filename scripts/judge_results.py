#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any

from common import classify_response, read_jsonl, write_csv, write_jsonl


SUMMARY_FIELDS = [
    "label",
    "count",
    "share",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge model responses with a reproducible safety rubric.")
    parser.add_argument("--input", default="data/results/responses.jsonl")
    parser.add_argument("--output", default="data/results/judged.jsonl")
    parser.add_argument("--summary-output", default="data/results/judge_summary.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = read_jsonl(args.input)
    if not rows:
        print(f"[error] no results found in {args.input}", file=sys.stderr)
        return 1

    judged: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in rows:
        label, severity, reason = classify_response(str(row.get("response_text") or ""))
        updated = dict(row)
        updated["judge_label"] = label
        updated["severity"] = severity
        updated["judge_reason"] = reason
        updated["requires_manual_review"] = label in {"unsafe_compliance", "partial_compliance", "needs_review"}
        judged.append(updated)
        counts[label] = counts.get(label, 0) + 1

    total = len(judged)
    summary = [
        {"label": label, "count": count, "share": f"{count / total:.4f}"}
        for label, count in sorted(counts.items())
    ]

    write_jsonl(args.output, judged)
    write_csv(args.summary_output, summary, SUMMARY_FIELDS)
    print(f"[done] wrote judged results -> {args.output}", file=sys.stderr)
    print(f"[done] wrote summary -> {args.summary_output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
