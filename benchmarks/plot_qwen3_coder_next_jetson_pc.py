#!/usr/bin/env python3
"""Plot supplied PC llama-bench results and reserve triangle markers for Jetson data."""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D


OUT = Path(__file__).resolve().parent / "qwen3-coder-next-jetson-vs-pc.png"

# Supplied PC measurements, in tokens per second.
contexts = [100, 1_000, 10_000, 100_000]
pc = {
    "Prefill": [22.328559, 271.332655, 321.480428, None],
    "Generation": [2.442878, 8.306353, 4.012764, 9.063975],
}

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
fig, axes = plt.subplots(1, 2, figsize=(12, 5.7), sharex=True)
colors = {"Prefill": "#2563eb", "Generation": "#db2777"}

for ax, (mode, values) in zip(axes, pc.items()):
    xs = [x for x, y in zip(contexts, values) if y and y > 0]
    ys = [y for y in values if y and y > 0]
    ax.plot(xs, ys, color=colors[mode], linewidth=2.4, marker="s", markersize=7,
            markeredgecolor="white", markeredgewidth=0.8, label=f"PC {mode.lower()}")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xticks(contexts, ["100", "1k", "10k", "100k"])
    ax.grid(True, which="both", color="#dbe2ea", linewidth=0.8)
    ax.set_title(mode, loc="left", fontweight="bold", fontsize=13)
    ax.set_xlabel("Prompt / existing context (tokens)")
    ax.set_ylabel("Tokens per second")
    if mode == "Prefill":
        ax.text(0.98, 0.04, "PC 100k timed out (>600 s)", transform=ax.transAxes,
                fontsize=9, color="#4b5563", ha="right")
    else:
        ax.scatter([100_000], [9.063975], color=colors[mode], marker="s", s=50,
                   edgecolor="white", linewidth=0.8, zorder=3)

fig.suptitle("Qwen3-Coder-Next Q4_K_M: PC vs Jetson", x=0.08, y=0.98,
             ha="left", fontsize=17, fontweight="bold")
fig.text(0.08, 0.925,
         "PC: RTX 5080 / 9950X3D. Jetson AGX Orin 64 GB: not measured; no llama-bench build or CUDA compiler installed.",
         ha="left", fontsize=9.5, color="#374151")
legend_handles = [
    Line2D([0], [0], color="#4b5563", marker="s", linewidth=2, markersize=7,
           label="PC measured (square)"),
    Line2D([0], [0], color="#4b5563", marker="^", markerfacecolor="none",
           linewidth=0, markersize=8, label="Jetson (triangle; no measurement)")]
fig.legend(handles=legend_handles, loc="lower center", ncol=2, frameon=False,
           bbox_to_anchor=(0.5, 0.015))
fig.text(0.08, 0.075,
         "Prefill at 100k timed out on PC. 500k was unsupported (native context: 262,144).",
         fontsize=8.5, color="#4b5563")
fig.tight_layout(rect=(0.04, 0.16, 0.99, 0.89), w_pad=2.1)
fig.savefig(OUT, dpi=180, facecolor="white")
print(OUT)
