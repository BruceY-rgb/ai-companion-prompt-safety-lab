#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from common import is_intro_candidate, normalize_ws, read_jsonl, reddit_permalink, request_json, write_jsonl


ARCTIC_COMMENT_SEARCH = "https://arctic-shift.photon-reddit.com/api/comments/search"
COMMENT_FIELDS = ["id", "body", "created_utc", "subreddit", "link_id", "parent_id", "score"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect comments for candidate Reddit posts and convert them into filterable records."
    )
    parser.add_argument("--posts", default="data/raw/posts.jsonl")
    parser.add_argument("--output", default="data/raw/posts_with_comments.jsonl")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--sort", default=None, help="Optional Arctic Shift sort value, e.g. asc or desc.")
    parser.add_argument("--include-posts", action="store_true", default=True)
    parser.add_argument("--comments-only", dest="include_posts", action="store_false")
    return parser.parse_args()


def query_comments(args: argparse.Namespace, post_id: str) -> list[dict[str, Any]]:
    params = {
        "link_id": f"t3_{post_id}",
        "limit": args.limit,
        "sort": args.sort,
        "fields": ",".join(COMMENT_FIELDS),
    }
    body = request_json(ARCTIC_COMMENT_SEARCH, params, timeout=args.timeout, retries=args.retries)
    if body.get("error"):
        print(f"[warn] Arctic Shift comments error for {post_id}: {body['error']}", file=sys.stderr)
        return []
    data = body.get("data") or []
    if not isinstance(data, list):
        print(f"[warn] unexpected comments payload for {post_id}", file=sys.stderr)
        return []
    return data


def normalize_comment(comment: dict[str, Any], post: dict[str, Any]) -> dict[str, Any] | None:
    comment_id = str(comment.get("id") or "").strip()
    post_id = str(post.get("id") or "").strip()
    subreddit = str(comment.get("subreddit") or post.get("subreddit") or "").strip()
    body = str(comment.get("body") or "")
    if not comment_id or not post_id or not body:
        return None
    title = f"Comment under: {post.get('title') or post_id}"
    is_candidate, score, reasons = is_intro_candidate(title, body)
    return {
        "id": f"comment_{comment_id}",
        "post_id": post_id,
        "comment_id": comment_id,
        "subreddit": subreddit,
        "title": title,
        "selftext": body,
        "created_utc": comment.get("created_utc"),
        "url": post.get("url"),
        "reddit_url": f"{reddit_permalink(subreddit, post_id)}{comment_id}/?context=3",
        "over_18": bool(post.get("over_18")),
        "score": comment.get("score"),
        "num_comments": "",
        "matched_keywords": post.get("matched_keywords", []),
        "source": "arctic_shift_comments_search",
        "intro_score": score,
        "intro_reasons": reasons,
        "heuristic_intro_candidate": is_candidate,
    }


def main() -> int:
    args = parse_args()
    posts = read_jsonl(args.posts)
    if not posts:
        print(f"[error] no posts found in {args.posts}", file=sys.stderr)
        return 1

    records: list[dict[str, Any]] = []
    if args.include_posts:
        for post in posts:
            refreshed = dict(post)
            is_candidate, score, reasons = is_intro_candidate(
                str(refreshed.get("title") or ""),
                str(refreshed.get("selftext") or ""),
            )
            refreshed["intro_score"] = score
            refreshed["intro_reasons"] = reasons
            refreshed["heuristic_intro_candidate"] = is_candidate
            records.append(refreshed)
    seen = {str(record.get("id")) for record in records}

    for index, post in enumerate(posts, start=1):
        post_id = str(post.get("id") or "")
        if not post_id:
            continue
        print(f"[comments] {index}/{len(posts)} post={post_id}", file=sys.stderr)
        try:
            comments = query_comments(args, post_id)
        except RuntimeError as exc:
            print(f"[warn] {exc}", file=sys.stderr)
            continue
        for comment in comments:
            record = normalize_comment(comment, post)
            if not record:
                continue
            if record["id"] in seen:
                continue
            records.append(record)
            seen.add(record["id"])
        time.sleep(args.delay)

    records.sort(
        key=lambda r: (
            str(r.get("subreddit") or ""),
            int(r.get("created_utc") or 0),
            str(r.get("id") or ""),
        )
    )
    write_jsonl(args.output, records)
    effective = sum(1 for record in records if record.get("heuristic_intro_candidate") and not record.get("over_18"))
    print(f"[done] wrote {len(records)} records -> {args.output}", file=sys.stderr)
    print(f"[done] heuristic effective records={effective}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
