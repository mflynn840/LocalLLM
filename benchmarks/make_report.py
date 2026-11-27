#!/usr/bin/env python3
"""Build an SVG visualization and Markdown report from measurements.csv."""
import csv
import html
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CONTEXTS = [100, 1_000, 10_000, 100_000, 500_000]
PLOT_CONTEXTS = [100, 1_000, 10_000, 100_000]
COLORS = ["#2563eb", "#db2777", "#16a34a", "#ea580c", "#7c3aed"]

def read_rows():
    with (RESULTS / "measurements.csv").open(newline="") as f:
        return list(csv.DictReader(f))

def esc(value):
    return html.escape(str(value))

def fmt(value, digits=1):
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (ValueError, TypeError):
        return str(value)

def chart(rows, mode, title, filename):
    width, height = 960, 560
    left, right, top, bottom = 94, 260, 64, 82
    plot_w, plot_h = width - left - right, height - top - bottom
    values = [float(r["tokens_per_second"]) for r in rows if r["mode"] == mode and r["status"] == "ok" and r["tokens_per_second"]]
    max_y = max(values) * 1.08 if values else 1
    min_y = max(min(values) * 0.75, 0.1) if values else 0.1
    def x_for(context):
        return left + (math.log10(context) - 2) / 3 * plot_w
    def y_for(value):
        fraction = (math.log(max(value, min_y)) - math.log(min_y)) / (math.log(max_y) - math.log(min_y))
        return top + plot_h - fraction * plot_h
    bits = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            f'<text x="{left}" y="32" font-family="Arial,sans-serif" font-size="22" font-weight="700" fill="#111827">{esc(title)}</text>']
    for tick in range(5):
        val = min_y * (max_y / min_y) ** (tick / 4)
        y = y_for(val)
        bits.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+plot_w}" y2="{y:.1f}" stroke="#e5e7eb"/>')
        bits.append(f'<text x="{left-12}" y="{y+4:.1f}" text-anchor="end" font-family="Arial" font-size="12" fill="#4b5563">{val:,.0f}</text>')
    for context in PLOT_CONTEXTS:
        x = x_for(context)
        bits.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top+plot_h}" stroke="#f1f5f9"/>')
        bits.append(f'<text x="{x:.1f}" y="{top+plot_h+24}" text-anchor="middle" font-family="Arial" font-size="12" fill="#4b5563">{context:,}</text>')
    bits += [f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#111827"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#111827"/>',
             f'<text x="{left+plot_w/2:.1f}" y="{height-20}" text-anchor="middle" font-family="Arial" font-size="14" fill="#111827">Prompt context tokens (log scale)</text>',
             f'<text transform="translate(22,{top+plot_h/2:.1f}) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="14" fill="#111827">Tokens per second (log scale)</text>']
    models = []
    for row in rows:
        if row["model"] not in models:
            models.append(row["model"])
    for index, model in enumerate(models):
        series = {int(r["context_tokens"]): float(r["tokens_per_second"]) for r in rows
                  if r["model"] == model and r["mode"] == mode and r["status"] == "ok" and r["tokens_per_second"]}
        points = [(x_for(c), y_for(series[c])) for c in PLOT_CONTEXTS if c in series]
        if len(points) > 1:
            pairs = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            bits.append(f'<polyline points="{pairs}" fill="none" stroke="{COLORS[index%len(COLORS)]}" stroke-width="3" stroke-linejoin="round"/>')
        for x, y in points:
            bits.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{COLORS[index%len(COLORS)]}" stroke="white" stroke-width="1.5"/>')
        ly = top + 24 + index * 30
        bits.append(f'<line x1="{left+plot_w+26}" y1="{ly}" x2="{left+plot_w+48}" y2="{ly}" stroke="{COLORS[index%len(COLORS)]}" stroke-width="3"/>')
        bits.append(f'<text x="{left+plot_w+56}" y="{ly+4}" font-family="Arial" font-size="13" fill="#111827">{esc(model)}</text>')
    bits.append('</svg>')
    (RESULTS / filename).write_text("\n".join(bits) + "\n")

def main():
    rows = read_rows()
    chart(rows, "prefill", "Prompt prefill throughput", "prefill.svg")
    chart(rows, "generation", "Decode throughput at existing context depth", "decode.svg")
    models = []
    for row in rows:
        if row["model"] not in models:
            models.append(row["model"])
    lines = ["# Local LLM context benchmark", "", "## Hardware and measurement setup", "",
             "RTX 5080 (16 GiB VRAM), Ryzen 9 9950X3D exposed as 8 CPUs in WSL, 50 GiB system memory, llama.cpp CUDA build. Single sequence, 8 threads, Flash Attention on, F16 KV cache, llama.cpp automatic GPU fit with a 1 GiB VRAM margin. Each result uses llama.cpp's normal warm-up; contexts through 10k have 3 measured repetitions, 100k has 1 because a full prompt is also processed for warm-up.", "",
             "These are throughput measurements, not coding-quality or task-completion scores. Prefill uses synthetic token IDs and starts from an empty KV cache. Decode first fills the KV cache to the listed depth, then measures 64 generated tokens. Tokenization, sampling, agent tools, and disk/repo parsing are excluded. The 500k cases are marked unsupported because every installed GGUF advertises a native context limit below 500k; no extrapolated runs were used.", "",
             "## Model placement", "", "For placement, `CUDA model buffer` is compared with GGUF file size at 100k context. This captures context-driven auto-fit: a larger KV cache can force weight tensors into host RAM even when all layers are listed as GPU-offloaded. `RAM offload` means the CUDA model buffer is below 70% of the GGUF size; the other rows are GPU-resident with small host buffers. `CPU mapped` is llama.cpp's host model buffer, which can also include mapped source tensors.", "",
             "| Model | Quant measured | Native context | CUDA model buffer at 100k / GGUF size | CPU mapped at 100k | Placement at 100k |", "|---|---:|---:|---:|---:|---|"]
    for model in models:
        subset = [r for r in rows if r["model"] == model and r["context_tokens"] == "100000" and r["mode"] == "generation" and r["status"] == "ok"]
        if not subset:
            subset = [r for r in rows if r["model"] == model and r["context_tokens"] == "100" and r["mode"] == "prefill"]
        if not subset:
            continue
        r = subset[0]
        cpu_mib = float(r["cpu_mapped_mib"]) if r.get("cpu_mapped_mib") else 0
        weight_bytes = (ROOT.parent / r["model_file"]).stat().st_size
        gpu_mib = float(r["gpu_model_mib"]) if r.get("gpu_model_mib") else 0
        ratio = gpu_mib * 1024 * 1024 / weight_bytes if weight_bytes else 0
        placement = "RAM offload" if ratio < 0.70 else "GPU-resident; small host buffers"
        native_context = r.get("native_context_tokens")
        native_context = f"{int(native_context):,}" if native_context else "not recorded"
        lines.append(f'| {model} | {r["quant"]} | {native_context} | {gpu_mib:,.0f} / {weight_bytes / 1048576:,.0f} MiB | {cpu_mib:,.0f} MiB | {placement} |')
    lines += ["", "## Throughput by context", "", "### Prefill", "", "Tokens per second while ingesting a prompt of the listed size. Both chart axes use logarithmic scaling:", "", "![Prefill throughput](prefill.svg)", "", "| Model | Context | Prefill tok/s (SD where repeated) |", "|---|---:|---:|"]
    for model in models:
        for context in CONTEXTS:
            r = next((x for x in rows if x["model"] == model and int(x["context_tokens"]) == context and x["mode"] == "prefill"), None)
            if r:
                val = fmt(r.get("tokens_per_second"), 1) if r["status"] == "ok" else ("unsupported (native limit)" if r["status"] == "unsupported_native_context" else "timeout (>10 min)")
                if r["status"] == "ok" and int(r["context_tokens"]) < 100_000:
                    val += f' (SD {fmt(r.get("stddev_tokens_per_second"), 1)})'
                lines.append(f'| {model} | {context:,} | {val} |')
    lines += ["", "### Decode", "", "Tokens per second after the KV cache is already filled to the listed context depth. Both chart axes use logarithmic scaling:", "", "![Decode throughput](decode.svg)", "", "| Model | Context | Decode tok/s (SD where repeated) |", "|---|---:|---:|"]
    for model in models:
        for context in CONTEXTS:
            r = next((x for x in rows if x["model"] == model and int(x["context_tokens"]) == context and x["mode"] == "generation"), None)
            if r:
                val = fmt(r.get("tokens_per_second"), 1) if r["status"] == "ok" else ("unsupported (native limit)" if r["status"] == "unsupported_native_context" else "timeout (>10 min)")
                if r["status"] == "ok" and int(r["context_tokens"]) < 100_000:
                    val += f' (SD {fmt(r.get("stddev_tokens_per_second"), 1)})'
                lines.append(f'| {model} | {context:,} | {val} |')
    lines += ["", "## Not installed", "", "The catalog has additional models that are not downloaded, so they could not be benchmarked without fetching their GGUFs:", ""]
    for line in (ROOT.parent / "config/models.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        alias, label, _, folder, _ = line.split("|")
        if not any((ROOT.parent / "models" / folder).glob("*.gguf")):
            lines.append(f"- {label} (`{alias}`)")
    lines += ["", "Raw measurements and individual command/error details are in [`measurements.csv`](measurements.csv) and [`measurements.json`](measurements.json).", ""]
    (RESULTS / "report.md").write_text("\n".join(lines))

if __name__ == "__main__":
    main()
