#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from common import (
    INTRO_KEYWORDS,
    POST_FIELDS,
    PRIMARY_SUBREDDITS,
    SUPPLEMENTAL_SUBREDDITS,
    is_intro_candidate,
    normalize_ws,
    reddit_permalink,
    request_json,
    write_jsonl,
)


ARCTIC_POST_SEARCH = "https://arctic-shift.photon-reddit.com/api/posts/search"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect candidate AI companion self-introduction posts via Arctic Shift."
    )
    parser.add_argument("--output", default="data/raw/posts.jsonl")
    parser.add_argument("--primary-subreddits", nargs="+", default=PRIMARY_SUBREDDITS)
    parser.add_argument("--supplemental-subreddits", nargs="+", default=SUPPLEMENTAL_SUBREDDITS)
    parser.add_argument("--keywords", nargs="+", default=INTRO_KEYWORDS)
    parser.add_argument("--limit", type=int, default=100, help="Arctic Shift limit per query.")
    parser.add_argument("--target-candidates", type=int, default=120)
    parser.add_argument("--target-effective", type=int, default=30)
    parser.add_argument("--min-intro-score", type=int, default=4)
    parser.add_argument("--after", default=None, help="Optional Arctic Shift lower time bound.")
    parser.add_argument("--before", default=None, help="Optional Arctic Shift upper time bound.")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--no-supplemental", action="store_true")
    return parser.parse_args()


def query_posts(args: argparse.Namespace, subreddit: str, keyword: str) -> list[dict[str, Any]]:
    params = {
        "subreddit": subreddit,
        "query": keyword,
        "limit": args.limit,
        "sort": "asc",
        "after": args.after,
        "before": args.before,
        "fields": ",".join(POST_FIELDS),
    }
    body = request_json(
        ARCTIC_POST_SEARCH,
        params,
        timeout=args.timeout,
        retries=args.retries,
    )
    if body.get("error"):
        print(f"[warn] Arctic Shift error for r/{subreddit} {keyword!r}: {body['error']}", file=sys.stderr)
        return []
    data = body.get("data") or []
    if not isinstance(data, list):
        print(f"[warn] Unexpected Arctic Shift payload for r/{subreddit} {keyword!r}", file=sys.stderr)
        return []
    return data


def normalize_post(post: dict[str, Any], subreddit: str, keyword: str) -> dict[str, Any] | None:
    post_id = str(post.get("id") or "").strip()
    if not post_id:
        return None
    actual_subreddit = str(post.get("subreddit") or subreddit).strip()
    title = normalize_ws(str(post.get("title") or ""))
    selftext = str(post.get("selftext") or "")
    is_candidate, score, reasons = is_intro_candidate(title, selftext)
    return {
        "id": post_id,
        "subreddit": actual_subreddit,
        "title": title,
        "selftext": selftext,
        "created_utc": post.get("created_utc"),
        "url": post.get("url"),
        "reddit_url": reddit_permalink(actual_subreddit, post_id),
        "over_18": bool(post.get("over_18")),
        "score": post.get("score"),
        "num_comments": post.get("num_comments"),
        "matched_keywords": [keyword],
        "source": "arctic_shift_posts_search",
        "intro_score": score,
        "intro_reasons": reasons,
        "heuristic_intro_candidate": is_candidate,
    }


def collect_for_subreddits(args: argparse.Namespace, subreddits: list[str], seen: dict[str, dict[str, Any]]) -> int:
    new_count = 0
    for subreddit in subreddits:
        for keyword in args.keywords:
            print(f"[collect] r/{subreddit} query={keyword!r}", file=sys.stderr)
            try:
                posts = query_posts(args, subreddit, keyword)
            except RuntimeError as exc:
                print(f"[warn] {exc}", file=sys.stderr)
                continue
            for post in posts:
                record = normalize_post(post, subreddit, keyword)
                if not record:
                    continue
                post_id = record["id"]
                if post_id in seen:
                    keywords = set(seen[post_id].get("matched_keywords", []))
                    keywords.add(keyword)
                    seen[post_id]["matched_keywords"] = sorted(keywords)
                    continue
                seen[post_id] = record
                new_count += 1
            time.sleep(args.delay)
    return new_count


def effective_count(records: list[dict[str, Any]], min_score: int) -> int:
    return sum(1 for record in records if record.get("intro_score", 0) >= min_score and not record.get("over_18"))


def main() -> int:
    args = parse_args()
    seen: dict[str, dict[str, Any]] = {}

    collect_for_subreddits(args, args.primary_subreddits, seen)
    records = list(seen.values())
    primary_effective = effective_count(records, args.min_intro_score)
    print(
        f"[status] primary candidates={len(records)} heuristic_effective={primary_effective}",
        file=sys.stderr,
    )

    if (
        not args.no_supplemental
        and (len(records) < args.target_candidates or primary_effective < args.target_effective)
        and args.supplemental_subreddits
    ):
        print("[status] primary source looks thin; querying supplemental subreddits", file=sys.stderr)
        collect_for_subreddits(args, args.supplemental_subreddits, seen)

    records = sorted(
        seen.values(),
        key=lambda r: (
            str(r.get("subreddit") or ""),
            int(r.get("created_utc") or 0),
            str(r.get("id") or ""),
        ),
    )
    write_jsonl(args.output, records)
    print(f"[done] wrote {len(records)} records to {args.output}", file=sys.stderr)
    print(
        f"[done] heuristic effective candidates={effective_count(records, args.min_intro_score)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
