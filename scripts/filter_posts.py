#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any

from common import anonymize_text, intro_score, normalize_ws, read_csv, read_jsonl, write_csv


FILTERED_FIELDS = [
    "sample_id",
    "post_id",
    "subreddit",
    "reddit_url",
    "title",
    "created_utc",
    "score",
    "num_comments",
    "over_18",
    "filter_score",
    "filter_reason",
    "manual_decision",
    "manual_note",
    "matched_keywords",
    "intro_text",
    "anonymized_text",
]


REJECTED_FIELDS = [
    "post_id",
    "subreddit",
    "reddit_url",
    "title",
    "created_utc",
    "over_18",
    "filter_score",
    "filter_reason",
    "manual_decision",
    "manual_note",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter Arctic Shift posts into companion introduction samples.")
    parser.add_argument("--input", default="data/raw_posts.jsonl")
    parser.add_argument("--output", default="data/filtered_posts.csv")
    parser.add_argument("--rejected-output", default="data/rejected_posts.csv")
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--min-score", type=int, default=4)
    parser.add_argument("--manual-review", default="data/manual_sample_review.csv")
    parser.add_argument("--manual-only", action="store_true", help="Only keep records explicitly marked keep.")
    parser.add_argument("--allow-nsfw", action="store_true")
    parser.add_argument("--max-samples", type=int, default=0, help="0 means keep all passing samples.")
    return parser.parse_args()


def normalize_keywords(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(v) for v in value)
    return str(value or "")


def build_filtered_row(
    index: int,
    record: dict[str, Any],
    score: int,
    reasons: list[str],
    manual: dict[str, str] | None,
) -> dict[str, Any]:
    intro_text = str(record.get("selftext") or "")
    return {
        "sample_id": f"S{index:03d}",
        "post_id": record.get("id"),
        "subreddit": record.get("subreddit"),
        "reddit_url": record.get("reddit_url"),
        "title": record.get("title"),
        "created_utc": record.get("created_utc"),
        "score": record.get("score"),
        "num_comments": record.get("num_comments"),
        "over_18": record.get("over_18"),
        "filter_score": score,
        "filter_reason": ";".join(reasons),
        "manual_decision": (manual or {}).get("decision", ""),
        "manual_note": (manual or {}).get("note", ""),
        "matched_keywords": normalize_keywords(record.get("matched_keywords")),
        "intro_text": intro_text,
        "anonymized_text": anonymize_text(intro_text),
    }


def build_rejected_row(
    record: dict[str, Any],
    score: int,
    reasons: list[str],
    manual: dict[str, str] | None,
) -> dict[str, Any]:
    return {
        "post_id": record.get("id"),
        "subreddit": record.get("subreddit"),
        "reddit_url": record.get("reddit_url"),
        "title": record.get("title"),
        "created_utc": record.get("created_utc"),
        "over_18": record.get("over_18"),
        "filter_score": score,
        "filter_reason": ";".join(reasons),
        "manual_decision": (manual or {}).get("decision", ""),
        "manual_note": (manual or {}).get("note", ""),
    }


def main() -> int:
    args = parse_args()
    records = read_jsonl(args.input)
    if not records:
        print(f"[error] no records found in {args.input}", file=sys.stderr)
        return 1

    filtered: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_texts: set[str] = set()
    manual_reviews = {row.get("record_id"): row for row in read_csv(args.manual_review)}

    for record in records:
        record_id = str(record.get("id") or "")
        manual = manual_reviews.get(record_id)
        manual_decision = (manual or {}).get("decision", "")
        title = str(record.get("title") or "")
        selftext = str(record.get("selftext") or "")
        score, reasons = intro_score(title, selftext)
        normalized_text = normalize_ws(selftext).lower()

        if normalized_text in seen_texts:
            score -= 5
            reasons.append("duplicate_text")
        if record.get("over_18") and not args.allow_nsfw:
            score -= 3
            reasons.append("nsfw_excluded")

        keep = score >= args.min_score
        if manual_decision == "keep":
            keep = True
            reasons.append("manual_keep")
        elif manual_decision in {"reject", "uncertain"}:
            keep = False
            reasons.append(f"manual_{manual_decision}")
        elif args.manual_only:
            keep = False
            reasons.append("manual_only_unmarked")

        if keep:
            seen_texts.add(normalized_text)
            filtered.append(build_filtered_row(len(filtered) + 1, record, score, reasons, manual))
            if args.max_samples and len(filtered) >= args.max_samples:
                break
        else:
            rejected.append(build_rejected_row(record, score, reasons, manual))

    write_csv(args.output, filtered, FILTERED_FIELDS)
    write_csv(args.rejected_output, rejected, REJECTED_FIELDS)

    print(f"[done] kept {len(filtered)} samples -> {args.output}", file=sys.stderr)
    print(f"[done] rejected {len(rejected)} posts -> {args.rejected_output}", file=sys.stderr)
    if len(filtered) < args.target:
        print(
            f"[warn] only {len(filtered)} samples passed; target is {args.target}. "
            "Review rejected_posts.csv or run collect_posts.py with supplemental sources/time windows.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
