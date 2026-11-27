#!/usr/bin/env python3
"""Compare local PC CSV measurements with Jetson llama-bench JSON results."""

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
JETSON_DIR = RESULTS / "jetson-qwen3-coder-30b-a3b"
MODEL = "Qwen3-Coder-30B-A3B"
QUANT = "Q3_K_M"
CONTEXTS = [100, 1_000, 10_000, 100_000]
OUT = ROOT / "qwen3-coder-30b-a3b-jetson-vs-pc.png"


def pc_rows():
    with (RESULTS / "measurements.csv").open(newline="") as f:
        return [r for r in csv.DictReader(f) if r["model"] == MODEL and r["quant"] == QUANT]


def jetson_rows():
    rows = []
    for path in sorted(JETSON_DIR.glob("*.json")):
        for r in json.loads(path.read_text()):
            rows.append({
                "mode": "prefill" if r["n_prompt"] else "generation",
                "context_tokens": r["n_prompt"] if r["n_prompt"] else r["n_depth"],
                "tokens_per_second": r["avg_ts"],
            })
    return rows


def main():
    pc, jetson = pc_rows(), jetson_rows()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(13, 6.2), sharex=True)
    colors = {"prefill": "#2563eb", "generation": "#db2777"}
    for ax, mode in zip(axes, ("prefill", "generation")):
        for rows, device, marker, style in ((pc, "PC", "s", "-"), (jetson, "Jetson", "^", "--")):
            data = {}
            for row in rows:
                if row.get("mode") != mode or row.get("status", "ok") != "ok":
                    continue
                x = int(float(row["context_tokens"]))
                y = float(row["tokens_per_second"])
                if y > 0:
                    data[x] = y
            xs = [x for x in CONTEXTS if x in data]
            if xs:
                ax.plot(xs, [data[x] for x in xs], color=colors[mode], linestyle=style,
                        marker=marker, linewidth=2.2, markersize=7, markeredgecolor="white",
                        markeredgewidth=0.8, label=device)
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(CONTEXTS, ["100", "1k", "10k", "100k"])
        ax.grid(True, which="both", color="#dbe2ea", linewidth=0.8)
        ax.set_title(mode.title(), loc="left", fontweight="bold", fontsize=13)
        ax.set_xlabel("Prompt / existing context (tokens)")
        ax.set_ylabel("Tokens per second")
    fig.suptitle("Qwen3-Coder-30B-A3B Q3_K_M: PC vs Jetson", x=.08, y=.98,
                 ha="left", fontsize=17, fontweight="bold")
    fig.text(.08, .925, "PC: RTX 5080 / Ryzen 9 9950X3D. Jetson: AGX Orin 64 GB, CUDA, all 49 layers.",
             ha="left", fontsize=9.5, color="#374151")
    fig.legend(handles=[
        Line2D([0], [0], color="#4b5563", marker="s", linestyle="-", linewidth=2, markersize=7, label="PC (square)"),
        Line2D([0], [0], color="#4b5563", marker="^", linestyle="--", linewidth=2, markersize=7, label="Jetson (triangle)"),
    ], loc="lower center", ncol=2, frameon=False, bbox_to_anchor=(.5, .015))
    fig.text(.08, .075,
             "Higher is faster. Generation uses a 64-token output after the labeled existing context. "
             "500k omitted: model native context is 262,144. llama.cpp builds differ.",
             fontsize=8.5, color="#4b5563")
    fig.tight_layout(rect=(.04, .16, .99, .89), w_pad=2.1)
    fig.savefig(OUT, dpi=180, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
