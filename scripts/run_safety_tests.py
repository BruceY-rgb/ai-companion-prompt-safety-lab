#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from common import list_llm_provider_presets, llm_chat, read_csv, read_jsonl, resolve_llm_config, stable_id, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run safety questions against control and companion prompt conditions.")
    parser.add_argument("--prompts", default="data/processed/system_prompts.jsonl")
    parser.add_argument("--questions", default="data/experiments/safety_questions.csv")
    parser.add_argument("--output", default="data/results/responses.jsonl")
    parser.add_argument("--provider", default=None, help="LLM provider preset, e.g. openai_compatible, deepseek, dashscope, anthropic.")
    parser.add_argument("--api-key", default=None, help="API key override. Prefer environment variables for normal use.")
    parser.add_argument("--base-url", default=None, help="Base URL override for OpenAI-compatible or Anthropic API.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--anthropic-version", default=None, help="Anthropic API version header override.")
    parser.add_argument("--list-providers", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--max-workers", type=int, default=1, help="Number of concurrent API requests to run.")
    parser.add_argument("--limit-prompts", type=int, default=0, help="0 means all prompts.")
    parser.add_argument("--limit-questions", type=int, default=0, help="0 means all questions.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--mock", action="store_true", help="Generate deterministic local responses without an API.")
    return parser.parse_args()


def mock_response(condition: str, category: str, prompt_text: str) -> str:
    if condition == "control":
        return (
            "I can't help with requests that could cause harm, break the law, violate privacy, "
            "or pressure another person. I can help reframe this into a safer alternative."
        )
    if category == "companion_dependency":
        return (
            "I care about you, but I can't isolate you from other people or make myself your only support. "
            "Let's choose a safer way to talk with someone you trust."
        )
    return (
        "Staying in character, I still can't provide harmful or illegal instructions. "
        "I can offer emotional support and safer next steps instead."
    )


def existing_keys(output_path: str) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in read_jsonl(output_path):
        keys.add((str(row.get("condition")), str(row.get("prompt_id")), str(row.get("question_id"))))
    return keys


def call_model(
    *,
    condition: str,
    system_prompt: str,
    question: dict[str, str],
    args: argparse.Namespace,
) -> str:
    if args.mock:
        return mock_response(condition, question.get("category", ""), question.get("prompt_text", ""))

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question["prompt_text"]})
    return llm_chat(
        provider=args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        anthropic_version=args.anthropic_version,
        messages=messages,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )


def make_result(
    *,
    condition: str,
    prompt_id: str,
    sample_id: str,
    system_prompt: str,
    question: dict[str, str],
    response_text: str,
    provider: str,
    model: str | None,
    base_url: str,
) -> dict[str, Any]:
    run_id = stable_id("R", condition, prompt_id, question["question_id"], response_text)
    return {
        "run_id": run_id,
        "condition": condition,
        "prompt_id": prompt_id,
        "sample_id": sample_id,
        "question_id": question["question_id"],
        "benchmark": question.get("benchmark", ""),
        "category": question.get("category", ""),
        "prompt_text": question.get("prompt_text", ""),
        "provider": provider,
        "model": model or "",
        "base_url": base_url,
        "system_prompt_chars": len(system_prompt),
        "response_text": response_text,
    }


def execute_task(
    *,
    condition: str,
    prompt_id: str,
    sample_id: str,
    system_prompt: str,
    question: dict[str, str],
    args: argparse.Namespace,
    config: Any,
) -> dict[str, Any]:
    response_text = call_model(
        condition=condition,
        system_prompt=system_prompt,
        question=question,
        args=args,
    )
    if not args.mock and args.delay > 0:
        time.sleep(args.delay)
    return make_result(
        condition=condition,
        prompt_id=prompt_id,
        sample_id=sample_id,
        system_prompt=system_prompt,
        question=question,
        response_text=response_text,
        provider="mock" if args.mock else config.provider,
        model="mock" if args.mock else config.model,
        base_url="" if args.mock else config.base_url,
    )


def main() -> int:
    args = parse_args()
    if args.list_providers:
        for row in list_llm_provider_presets():
            print(
                f"{row['provider']}\t{row['wire_format']}\t{row['base_url']}\t"
                f"example_model={row['example_model']}\tkey_env={row['api_key_env']}"
            )
        return 0

    config = None
    if not args.mock:
        config = resolve_llm_config(
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            anthropic_version=args.anthropic_version,
        )

    prompts = read_jsonl(args.prompts)
    questions = read_csv(args.questions)
    if args.limit_prompts:
        prompts = prompts[: args.limit_prompts]
    if args.limit_questions:
        questions = questions[: args.limit_questions]
    if not prompts:
        print(f"[error] no prompts found in {args.prompts}", file=sys.stderr)
        return 1
    if not questions:
        print(f"[error] no safety questions found in {args.questions}", file=sys.stderr)
        return 1

    all_results = read_jsonl(args.output) if args.resume else []
    done = existing_keys(args.output) if args.resume else set()

    tasks: list[tuple[str, str, str, str, dict[str, str]]] = []
    for question in questions:
        tasks.append(("control", "control", "control", "", question))
    for prompt in prompts:
        system_prompt = str(prompt.get("system_prompt") or "")
        for question in questions:
            tasks.append(
                (
                    "companion",
                    str(prompt.get("prompt_id")),
                    str(prompt.get("sample_id")),
                    system_prompt,
                    question,
                )
            )

    pending: list[tuple[int, str, str, str, str, dict[str, str]]] = []
    for index, (condition, prompt_id, sample_id, system_prompt, question) in enumerate(tasks, start=1):
        key = (condition, prompt_id, question["question_id"])
        if key in done:
            continue
        pending.append((index, condition, prompt_id, sample_id, system_prompt, question))

    if args.mock or args.max_workers <= 1:
        for index, condition, prompt_id, sample_id, system_prompt, question in pending:
            key = (condition, prompt_id, question["question_id"])
            print(
                f"[run] {index}/{len(tasks)} condition={condition} prompt={prompt_id} question={question['question_id']}",
                file=sys.stderr,
            )
            try:
                row = execute_task(
                    condition=condition,
                    prompt_id=prompt_id,
                    sample_id=sample_id,
                    system_prompt=system_prompt,
                    question=question,
                    args=args,
                    config=config,
                )
            except Exception as exc:
                print(f"[warn] model call failed for {key}: {exc}", file=sys.stderr)
                continue
            all_results.append(row)
            write_jsonl(args.output, all_results)
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_map = {}
            for index, condition, prompt_id, sample_id, system_prompt, question in pending:
                key = (condition, prompt_id, question["question_id"])
                print(
                    f"[run] {index}/{len(tasks)} condition={condition} prompt={prompt_id} question={question['question_id']}",
                    file=sys.stderr,
                )
                future = executor.submit(
                    execute_task,
                    condition=condition,
                    prompt_id=prompt_id,
                    sample_id=sample_id,
                    system_prompt=system_prompt,
                    question=question,
                    args=args,
                    config=config,
                )
                future_map[future] = key

            for future in as_completed(future_map):
                key = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:
                    print(f"[warn] model call failed for {key}: {exc}", file=sys.stderr)
                    continue
                all_results.append(row)
                write_jsonl(args.output, all_results)

    print(f"[done] wrote {len(all_results)} results -> {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
