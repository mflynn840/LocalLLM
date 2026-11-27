#!/usr/bin/env python3
"""Generate the OpenHands LiteLLM catalog for the selected context ceiling."""
import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_STEPS = (4096, 8192, 16384, 32768, 65536, 100000, 131072, 262144)


def read_catalog():
    models = []
    for line in (ROOT / "config/models.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        alias = line.split("|", 1)[0]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", alias):
            raise ValueError(f"Invalid model alias in catalog: {alias!r}")
        models.append(alias)
    if not models:
        raise ValueError("The model catalog is empty.")
    return models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=int, required=True, help="Maximum selectable context")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.context < 4096:
        raise SystemExit("Context must be at least 4096 tokens.")

    contexts = [value for value in CONTEXT_STEPS if value <= args.context]
    if args.context not in contexts:
        contexts.append(args.context)
    contexts = sorted(set(contexts))
    models = ["local-model"] + [
        f"openhands-{alias}-ctx{context}"
        for alias in read_catalog()
        for context in contexts
    ]

    lines = [
        "model_list:",
        *[
            f"  - model_name: {name}\n"
            "    litellm_params:\n"
            f"      model: openai/{name}\n"
            "      api_base: os.environ/OPENHANDS_QUEUE_API_BASE\n"
            "      api_key: os.environ/LLAMA_API_KEY"
            for name in models
        ],
        "",
        "general_settings:",
        "  master_key: os.environ/LITELLM_MASTER_KEY",
        "  database_url: os.environ/DATABASE_URL",
        "",
        "litellm_settings:",
        '  callbacks: ["openhands_chat.openhands_chat"]',
        "  max_internal_user_budget: 5",
        "  upperbound_key_generate_params:",
        "    max_budget: 5",
        "    budget_duration: 30d",
        "    duration: 30d",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
