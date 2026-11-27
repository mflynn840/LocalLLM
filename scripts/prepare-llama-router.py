#!/usr/bin/env python3
"""Write llama.cpp router presets for catalog models and selectable contexts."""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_STEPS = (4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144)


def read_catalog():
    items = []
    for line in (ROOT / "config/models.tsv").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        if len(fields) not in (5, 6):
            raise ValueError(f"Invalid model catalog row: {line!r}")
        alias, label, repo, folder, quant = fields[:5]
        quants = fields[5].split(",") if len(fields) == 6 and fields[5] else [quant]
        items.append((alias, label, repo, folder, quant, quants))
    return items


def find_file(folder, quant):
    base = ROOT / "models" / folder
    files = []
    if base.is_dir():
        for path in base.rglob("*.gguf"):
            name = path.name.lower()
            index = name.find(quant.lower())
            if index < 0:
                continue
            if not quant.upper().startswith("UD-") and name[max(0, index - 3):index] == "ud-":
                continue
            files.append(path)
        files.sort()
    shards = [p for p in files if re.search(r"00001-of-.*\.gguf$", p.name)]
    files = shards or files
    return files[0] if files else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Selected default catalog alias")
    parser.add_argument("--quant", default="", help="Selected default quantization")
    parser.add_argument("--context", type=int, required=True, help="Router context ceiling")
    parser.add_argument("--model-root", default="/models", help="Model path visible to llama-server")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    models = read_catalog()
    by_alias = {item[0]: item for item in models}
    if args.model not in by_alias:
        raise SystemExit(f"Unknown model alias: {args.model}")
    selected = by_alias[args.model]
    selected_quant = args.quant or selected[4]
    if selected_quant not in selected[5]:
        raise SystemExit(f"Unsupported quantization {selected_quant!r} for {args.model}")
    contexts = [value for value in CONTEXT_STEPS if value <= args.context]
    if args.context not in contexts:
        contexts.append(args.context)
    contexts = sorted(set(contexts))
    lines = ["version = 1", "", "[*]", "fit = on", "n-gpu-layers = auto", "parallel = 1", "flash-attn = on", ""]

    selected_file = find_file(selected[3], selected_quant)
    lines.extend(["[local-model]", f"c = {args.context}"])
    if selected_file:
        lines.append(f"model = {args.model_root.rstrip('/')}/{selected_file.relative_to(ROOT / 'models')}")
    else:
        lines.append(f"hf-repo = {selected[2]}:{selected_quant}")
    lines.append("")

    for alias, _label, repo, folder, default_quant, quants in models:
        for quant in quants:
            model_file = find_file(folder, quant)
            source = (f"model = {args.model_root.rstrip('/')}/{model_file.relative_to(ROOT / 'models')}"
                      if model_file else f"hf-repo = {repo}:{quant}")
            for context in contexts:
                lines.extend([f"[openhands-{alias}-q{quant}-ctx{context}]", source, f"c = {context}", ""])
                if quant == default_quant:
                    # Keep the pre-quant-selector model IDs usable by saved profiles.
                    lines.extend([f"[openhands-{alias}-ctx{context}]", source, f"c = {context}", ""])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
