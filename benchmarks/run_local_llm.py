#!/usr/bin/env python3
"""Benchmark installed LocalLLM GGUFs with the host llama.cpp llama-bench."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/results"
DEFAULT_CONTEXTS = [100, 1_000, 10_000, 100_000, 500_000]
DEFAULT_MODES = ["prefill", "generation"]
GEN_TOKENS = 64
REPETITIONS = 3
TIMEOUT_SECONDS = 600


def catalog() -> dict[str, tuple[str, str, str]]:
    entries = {}
    for line in (ROOT / "config/models.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        alias, label, _repo, folder, default_quant = line.split("|")
        entries[alias] = (label, folder, default_quant)
    return entries


def installed_models(requested: list[str], quant: str | None) -> list[dict]:
    known = catalog()
    names = requested or list(known)
    found = []
    for name in names:
        label, folder, default_quant = known.get(name, (name, name, ""))
        selected_quant = quant or default_quant
        directory = ROOT / "models" / folder
        pattern = f"*{selected_quant}*.gguf" if selected_quant else "*.gguf"
        files = sorted(directory.rglob(pattern)) if directory.is_dir() else []
        # Multi-file GGUFs are opened through their first shard.
        shards = [p for p in files if re.search(r"00001-of-.*\.gguf$", p.name)]
        if shards:
            files = shards
        if not files:
            if requested:
                print(f"Skipping {name}: no installed GGUF matching {pattern} under {directory}", file=sys.stderr)
            continue
        if len(files) > 1:
            raise SystemExit(f"{name} has multiple matching GGUF files; pass --quant to choose one.")
        model_file = files[0]
        quant_label = selected_quant or re.search(r"((?:IQ|Q|MXFP|UD-Q)[A-Za-z0-9_-]+)\.gguf$", model_file.name)
        quant_label = quant_label if isinstance(quant_label, str) else (quant_label.group(1) if quant_label else "unknown")
        found.append({"model": label, "alias": name, "quant": quant_label,
                      "model_file": str(model_file.relative_to(ROOT))})
    return found


def bench(model_file: str, context: int, mode: str, args: argparse.Namespace) -> dict:
    path = ROOT / model_file
    repetitions = 1 if context >= 100_000 else args.repetitions
    if mode == "prefill":
        bench_args = ["-p", str(context), "-n", "0", "-fitc", str(context)]
    else:
        bench_args = ["-p", "0", "-n", str(args.generation_tokens), "-d", str(context),
                      "-fitc", str(context + args.generation_tokens)]
    cmd = [str(args.binary), "-m", str(path), *bench_args, "-r", str(repetitions),
           "-t", str(args.threads), "-ngl", args.gpu_layers, "-fa", args.flash_attention,
           "-fitt", str(args.fit_margin_mib), "-o", "json", "-v"]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{args.binary.parent}:{env.get('LD_LIBRARY_PATH', '')}"
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True,
                              timeout=args.timeout)
        try:
            records = json.loads(proc.stdout)
        except json.JSONDecodeError:
            records = []
        selected = ([r for r in records if r.get("n_prompt") == context and r.get("n_gen") == 0]
                    if mode == "prefill" else
                    [r for r in records if r.get("n_depth") == context and r.get("n_prompt") == 0
                     and r.get("n_gen") == args.generation_tokens])
        record = selected[-1] if selected else {}
        logs = proc.stderr
        offloads = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", logs)
        cpu_buffers = re.findall(r"CPU_Mapped model buffer size\s*=\s*([\d.]+) MiB", logs)
        gpu_buffers = re.findall(r"CUDA0 model buffer size\s*=\s*([\d.]+) MiB", logs)
        return {
            "status": "ok" if proc.returncode == 0 and record else "failed",
            "tokens_per_second": record.get("avg_ts"),
            "stddev_tokens_per_second": record.get("stddev_ts"),
            "n_gpu_layers": record.get("n_gpu_layers"),
            "offloaded_layers": offloads[-1] if offloads else "",
            "cpu_mapped_mib": float(cpu_buffers[-1]) if cpu_buffers else None,
            "gpu_model_mib": float(gpu_buffers[-1]) if gpu_buffers else None,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "return_code": proc.returncode,
            "error": (proc.stderr + "\n" + proc.stdout)[-2500:] if proc.returncode != 0 or not record else "",
            "command": shlex.join(cmd),
        }
    except subprocess.TimeoutExpired as exc:
        return {"status": "timeout", "tokens_per_second": None, "stddev_tokens_per_second": None,
                "n_gpu_layers": None, "offloaded_layers": "", "cpu_mapped_mib": None,
                "gpu_model_mib": None, "elapsed_seconds": args.timeout, "return_code": None,
                "error": str(exc), "command": shlex.join(cmd)}


def save(rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "measurements.json").write_text(json.dumps(rows, indent=2) + "\n")
    if rows:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        with (OUT / "measurements.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], help="Catalog alias; repeat to benchmark several. Defaults to all installed catalog models.")
    parser.add_argument("--quant", help="Only use GGUF files matching this quantization string.")
    parser.add_argument("--contexts", nargs="+", type=int, default=DEFAULT_CONTEXTS, metavar="TOKENS")
    parser.add_argument("--mode", choices=["prefill", "generation", "both"], default="both")
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--generation-tokens", type=int, default=GEN_TOKENS)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--gpu-layers", default="999")
    parser.add_argument("--flash-attention", choices=["on", "off"], default="on")
    parser.add_argument("--fit-margin-mib", type=int, default=1024)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    parser.add_argument("--binary", type=Path, default=Path(os.environ.get("LLAMA_BENCH_PATH", ROOT / "llama.cpp/build/bin/llama-bench")))
    parser.add_argument("--no-resume", action="store_true", help="Rerun measurements already present in results.")
    args = parser.parse_args()
    if not args.binary.is_file() or not os.access(args.binary, os.X_OK):
        parser.error(f"llama-bench is not executable: {args.binary} (set LLAMA_BENCH_PATH to override)")
    if any(context <= 0 for context in args.contexts) or min(args.repetitions, args.generation_tokens, args.threads, args.timeout) <= 0:
        parser.error("contexts, repetitions, generation tokens, threads, and timeout must be positive")
    models = installed_models(args.model, args.quant)
    if not models:
        parser.error("no matching installed GGUF models found")
    json_path = OUT / "measurements.json"
    rows = [] if args.no_resume or not json_path.exists() else json.loads(json_path.read_text())
    completed = {(r["model_file"], int(r["context_tokens"]), r["mode"]) for r in rows}
    modes = DEFAULT_MODES if args.mode == "both" else [args.mode]
    for model in models:
        for context in args.contexts:
            for mode in modes:
                key = (model["model_file"], context, mode)
                if key in completed:
                    continue
                print(f"{model['model']} ({model['model_file']}) | {context:,} | {mode}", flush=True)
                result = bench(model["model_file"], context, mode, args)
                rows.append({**model, "context_tokens": context, "mode": mode,
                             "generation_tokens": args.generation_tokens if mode == "generation" else 0,
                             **result})
                save(rows)
                print(f"  {result['status']} {result.get('tokens_per_second')} tok/s; elapsed={result['elapsed_seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
