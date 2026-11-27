#!/usr/bin/env python3
"""Run the installed GGUF context throughput sweep with llama.cpp."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "llama.cpp/build/bin/llama-bench"
OUT = ROOT / "benchmarks/results"
CONTEXTS = [100, 1_000, 10_000, 100_000, 500_000]
GEN_TOKENS = 64
REPETITIONS = 3
TIMEOUT_SECONDS = 600
MODELS = [
    ("Qwen3-Coder-30B-A3B", "Q3_K_M", "models/qwen3-coder-30b-a3b/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf", 262_144),
    ("gpt-oss-20b", "Q4_K_M", "models/gpt-oss-20b/gpt-oss-20b-Q4_K_M.gguf", 131_072),
    ("Qwen3.5-9B", "Q4_K_M", "models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf", 262_144),
    ("Gemma-4-12B", "Q4_K_M", "models/gemma-4-12b/gemma-4-12b-it-Q4_K_M.gguf", 262_144),
    ("Qwen3-Coder-Next", "Q4_K_M", "models/qwen3-coder-next/Qwen3-Coder-Next-Q4_K_M.gguf", 262_144),
]

def bench(model: str, context: int, mode: str) -> dict:
    path = ROOT / model
    repetitions = 1 if context >= 100_000 else REPETITIONS
    if mode == "prefill":
        args = ["-p", str(context), "-n", "0", "-fitc", str(context)]
    else:
        args = ["-p", "0", "-n", str(GEN_TOKENS), "-d", str(context), "-fitc", str(context + GEN_TOKENS)]
    cmd = [str(BIN), "-m", str(path), *args, "-r", str(repetitions), "-t", "8", "-ngl", "999", "-fa", "on", "-fitt", "1024", "-o", "json", "-v"]
    env = dict(os.environ, LD_LIBRARY_PATH=f"{ROOT / 'llama.cpp/build/bin'}:{os.environ.get('LD_LIBRARY_PATH', '')}")
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=TIMEOUT_SECONDS)
        elapsed = round(time.monotonic() - started, 2)
        try:
            records = json.loads(proc.stdout)
        except json.JSONDecodeError:
            records = []
        selected = [r for r in records if (r.get("n_prompt") == context and r.get("n_gen") == 0) if mode == "prefill"] if mode == "prefill" else [r for r in records if r.get("n_depth") == context and r.get("n_prompt") == 0 and r.get("n_gen") == GEN_TOKENS]
        record = selected[-1] if selected else {}
        logs = proc.stderr
        offloads = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", logs)
        cpu_buffers = re.findall(r"CPU_Mapped model buffer size\s*=\s*([\d.]+) MiB", logs)
        gpu_buffers = re.findall(r"CUDA0 model buffer size\s*=\s*([\d.]+) MiB", logs)
        row = {
            "status": "ok" if proc.returncode == 0 and record else "failed",
            "tokens_per_second": record.get("avg_ts"),
            "stddev_tokens_per_second": record.get("stddev_ts"),
            "n_gpu_layers": record.get("n_gpu_layers"),
            "offloaded_layers": offloads[-1] if offloads else "",
            "cpu_mapped_mib": float(cpu_buffers[-1]) if cpu_buffers else None,
            "gpu_model_mib": float(gpu_buffers[-1]) if gpu_buffers else None,
            "elapsed_seconds": elapsed,
            "return_code": proc.returncode,
            "error": (proc.stderr + "\n" + proc.stdout)[-2500:] if proc.returncode != 0 or not record else "",
            "command": " ".join(cmd),
        }
    except subprocess.TimeoutExpired as e:
        row = {"status": "timeout", "tokens_per_second": None, "stddev_tokens_per_second": None,
               "n_gpu_layers": None, "offloaded_layers": "", "cpu_mapped_mib": None, "gpu_model_mib": None,
               "elapsed_seconds": TIMEOUT_SECONDS, "return_code": None,
               "error": str(e), "command": " ".join(cmd)}
    return row

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "measurements.csv"
    json_path = OUT / "measurements.json"
    rows = json.loads(json_path.read_text()) if json_path.exists() else []
    completed = {(x["model"], x["context_tokens"], x["mode"]) for x in rows}
    for label, quant, relpath, native_context in MODELS:
        if not (ROOT / relpath).is_file():
            continue
        for context in CONTEXTS:
            for mode in ("prefill", "generation"):
                key = (label, context, mode)
                if key in completed:
                    continue
                if context > native_context:
                    result = {"status": "unsupported_native_context", "tokens_per_second": None,
                              "stddev_tokens_per_second": None, "n_gpu_layers": None, "offloaded_layers": "",
                              "cpu_mapped_mib": None, "gpu_model_mib": None, "elapsed_seconds": 0,
                              "return_code": None, "error": f"GGUF native context is {native_context:,} tokens"}
                else:
                    print(f"{label} {quant} | {context:,} | {mode}", flush=True)
                    result = bench(relpath, context, mode)
                rows.append({"model": label, "quant": quant, "model_file": relpath,
                             "native_context_tokens": native_context, "context_tokens": context,
                             "mode": mode, **result})
                json_path.write_text(json.dumps(rows, indent=2) + "\n")
                with csv_path.open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"  {result['status']} {result.get('tokens_per_second')} tok/s; GPU layers={result.get('offloaded_layers')}; CPU mapped={result.get('cpu_mapped_mib')} MiB", flush=True)

if __name__ == "__main__":
    main()
