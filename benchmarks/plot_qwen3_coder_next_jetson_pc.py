#!/usr/bin/env python3
"""Plot the supplied PC and measured Jetson Qwen3-Coder-Next results."""

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
MODEL = "Qwen3-Coder-Next"
QUANT = "Q4_K_M"
OUT = ROOT / "qwen3-coder-next-jetson-vs-pc.png"
JETSON_DIR = RESULTS / "jetson-qwen3-coder-next"


def load_pc():
    with (RESULTS / "measurements.csv").open(newline="") as f:
        rows = csv.DictReader(f)
        return [r for r in rows if r["model"] == MODEL and r["quant"] == QUANT]


def load_jetson():
    rows = []
    for path in sorted(JETSON_DIR.glob("*.json")):
        for row in json.loads(path.read_text()):
            row["mode"] = "prefill" if row["n_prompt"] else "generation"
            row["context_tokens"] = row["n_prompt"] if row["n_prompt"] else row["n_depth"]
            row["tokens_per_second"] = row["avg_ts"]
            rows.append(row)
    return rows


def main():
    pc_rows = load_pc()
    jetson_rows = load_jetson()
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(12.8, 6.1), sharex=True)
    colors = {"prefill": "#2563eb", "generation": "#db2777"}
    contexts = [100, 1_000, 10_000, 100_000]

    for ax, mode in zip(axes, ["prefill", "generation"]):
        for rows, device, marker, linestyle in [
            (pc_rows, "PC", "s", "-"),
            (jetson_rows, "Jetson", "^", "--"),
        ]:
            series = {
                int(r["context_tokens"]): float(r["tokens_per_second"])
                for r in rows
                if r["mode"] == mode
                and (r.get("status", "ok") == "ok")
                and r.get("tokens_per_second") not in (None, "")
                and float(r["tokens_per_second"]) > 0
            }
            xs = [x for x in contexts if x in series]
            ys = [series[x] for x in xs]
            if xs:
                ax.plot(xs, ys, color=colors[mode], linewidth=2.2, linestyle=linestyle,
                        marker=marker, markersize=7, markeredgecolor="white",
                        markeredgewidth=0.8, label=device)

        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xticks(contexts, ["100", "1k", "10k", "100k"])
        ax.grid(True, which="both", color="#dbe2ea", linewidth=0.8)
        ax.set_title(mode.title(), loc="left", fontweight="bold", fontsize=13)
        ax.set_xlabel("Prompt / existing context (tokens)")
        ax.set_ylabel("Tokens per second")
        if mode == "prefill":
            ax.text(0.98, 0.04, "PC 100k timed out (>600 s)", transform=ax.transAxes,
                    fontsize=9, color="#4b5563", ha="right")

    fig.suptitle("Qwen3-Coder-Next Q4_K_M: PC and Jetson", x=0.08, y=0.98,
                 ha="left", fontsize=17, fontweight="bold")
    fig.text(0.08, 0.925,
             "PC: RTX 5080 / Ryzen 9 9950X3D. Jetson: AGX Orin 64 GB; all 49 layers on CUDA.",
             ha="left", fontsize=9.5, color="#374151")
    legend_handles = [
        Line2D([0], [0], color="#4b5563", marker="s", linestyle="-", linewidth=2,
               markersize=7, label="PC (square)"),
        Line2D([0], [0], color="#4b5563", marker="^", linestyle="--", linewidth=2,
               markersize=7, label="Jetson (triangle)"),
    ]
    fig.legend(handles=legend_handles, loc="lower center", ncol=2, frameon=False,
               bbox_to_anchor=(0.5, 0.015))
    fig.text(0.08, 0.075,
             "500k unsupported (GGUF native context: 262,144). Jetson build: llama.cpp a07831a; PC build revision differs.",
             fontsize=8.5, color="#4b5563")
    fig.tight_layout(rect=(0.04, 0.16, 0.99, 0.89), w_pad=2.1)
    fig.savefig(OUT, dpi=180, facecolor="white")
    print(OUT)


if __name__ == "__main__":
    main()
