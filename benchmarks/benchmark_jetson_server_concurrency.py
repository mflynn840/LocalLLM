#!/usr/bin/env python3
"""Measure concurrent completion latency and throughput from a running llama-server."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def post_json(url: str, payload: dict, timeout: float = 1200.0) -> dict:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def tokenize_count(base_url: str, text: str) -> int:
    result = post_json(f"{base_url}/tokenize", {"content": text})
    return len(result["tokens"])


def make_prompt(base_url: str, target: int) -> tuple[str, int]:
    unit = "// Explain this function and suggest one edge case.\nint add(int a, int b) { return a + b; }\n"
    candidates = []
    for copies in range(1, 300):
        prompt = unit * copies
        count = tokenize_count(base_url, prompt)
        candidates.append((abs(count - target), prompt, count))
        if count >= target:
            break
    _, prompt, count = min(candidates, key=lambda item: item[0])
    return prompt, count


def percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    index = max(0, min(len(values) - 1, round((len(values) - 1) * pct)))
    return values[index]


def request_once(base_url: str, prompt: str, max_tokens: int, barrier=None) -> dict:
    if barrier is not None:
        barrier.wait()
    started = time.perf_counter()
    result = post_json(
        f"{base_url}/completion",
        {
            "prompt": prompt,
            "n_predict": max_tokens,
            "temperature": 0.8,
            "top_k": 40,
            "ignore_eos": True,
            "cache_prompt": False,
        },
    )
    latency = time.perf_counter() - started
    timing = result.get("timings", {})
    return {
        "latency_seconds": latency,
        "prompt_tokens": int(timing.get("prompt_n", result.get("tokens_evaluated", 0))),
        "generated_tokens": int(timing.get("predicted_n", result.get("tokens_predicted", 0))),
        "prompt_tokens_per_second": timing.get("prompt_per_second"),
        "generation_tokens_per_second": timing.get("predicted_per_second"),
    }


def run_wave(base_url: str, prompt: str, concurrency: int, max_tokens: int) -> dict:
    barrier = threading.Barrier(concurrency)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(request_once, base_url, prompt, max_tokens, barrier)
                   for _ in range(concurrency)]
        requests = [future.result() for future in futures]
    wall = time.perf_counter() - started
    generated = sum(row["generated_tokens"] for row in requests)
    prompted = sum(row["prompt_tokens"] for row in requests)
    latencies = [row["latency_seconds"] for row in requests]
    server_gen_rates = [row["generation_tokens_per_second"] for row in requests
                        if row["generation_tokens_per_second"] is not None]
    server_prompt_rates = [row["prompt_tokens_per_second"] for row in requests
                           if row["prompt_tokens_per_second"] is not None]
    return {
        "concurrent_users": concurrency,
        "wall_seconds": wall,
        "requests_completed": len(requests),
        "prompt_tokens_total": prompted,
        "generated_tokens_total": generated,
        "aggregate_generated_tokens_per_second": generated / wall,
        "per_user_generated_tokens_per_second": generated / wall / concurrency,
        "mean_latency_seconds": statistics.mean(latencies),
        "p50_latency_seconds": percentile(latencies, 0.50),
        "p95_latency_seconds": percentile(latencies, 0.95),
        "mean_server_prompt_tokens_per_second": statistics.mean(server_prompt_rates)
        if server_prompt_rates else None,
        "mean_server_generation_tokens_per_second": statistics.mean(server_gen_rates)
        if server_gen_rates else None,
        "request_details": requests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--clients", default="1,2,4,8")
    parser.add_argument("--server-slots", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--prompt-tokens", type=int, default=512)
    parser.add_argument("--generation-tokens", type=int, default=64)
    parser.add_argument("--out", default="/tmp/jetson-server-concurrency.json")
    args = parser.parse_args()
    clients = [int(value) for value in args.clients.split(",")]

    prompt, actual_prompt_tokens = make_prompt(args.base_url, args.prompt_tokens)
    request_once(args.base_url, prompt, min(16, args.generation_tokens))  # warm-up

    results = []
    for concurrency in clients:
        for repetition in range(1, args.rounds + 1):
            row = run_wave(args.base_url, prompt, concurrency, args.generation_tokens)
            row["repetition"] = repetition
            results.append(row)
            print(
                f"users={concurrency} round={repetition} "
                f"aggregate={row['aggregate_generated_tokens_per_second']:.2f} tok/s "
                f"mean_latency={row['mean_latency_seconds']:.2f}s",
                flush=True,
            )

    output = {
        "base_url": args.base_url,
        "prompt_target_tokens": args.prompt_tokens,
        "prompt_actual_tokens": actual_prompt_tokens,
        "generation_tokens_requested": args.generation_tokens,
        "server_slots": args.server_slots,
        "rounds": args.rounds,
        "measurements": results,
    }
    path = Path(args.out)
    path.write_text(json.dumps(output, indent=2) + "\n")
    print(f"saved {path}")


if __name__ == "__main__":
    main()
