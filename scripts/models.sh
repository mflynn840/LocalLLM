#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CATALOG="$ROOT/config/models.tsv"
MODEL_ROOT="${MODEL_ROOT:-$ROOT/models}"

usage() {
    cat <<'EOF'
Usage:
  scripts/models.sh list
  scripts/models.sh download --model ALIAS[:QUANT] [--model ALIAS[:QUANT] ...] [--quant QUANT]
  scripts/models.sh download --repo HF_REPO --name LOCAL_ALIAS --include FILE_GLOB
  scripts/models.sh resolve ALIAS [QUANT]

Examples:
  scripts/models.sh download --model qwen3.5-9b --model qwen3-coder-30b-a3b:Q3_K_M
  scripts/models.sh download --repo org/model-GGUF --name my-model --include '*Q4_K_M*.gguf'
EOF
}

catalog_entry() {
    local wanted="$1"
    local alias label repo folder default_quant
    while IFS='|' read -r alias label repo folder default_quant; do
        [[ -z "$alias" || "$alias" == \#* ]] && continue
        if [[ "$alias" == "$wanted" ]]; then
            printf '%s|%s|%s|%s\n' "$label" "$repo" "$folder" "$default_quant"
            return 0
        fi
    done < "$CATALOG"
    return 1
}

valid_alias() {
    [[ "$1" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]
}

quant_pattern() {
    printf '*%s*.gguf\n' "$1"
}

find_model_file() {
    local alias="$1" quant="${2:-}" entry label repo folder default_quant dir candidate
    if entry="$(catalog_entry "$alias")"; then
        IFS='|' read -r label repo folder default_quant <<< "$entry"
        quant="${quant:-$default_quant}"
    else
        valid_alias "$alias" || {
            echo "Invalid model alias: $alias" >&2
            return 2
        }
        folder="$alias"
        quant="${quant:-}"
    fi

    dir="$MODEL_ROOT/$folder"
    [[ -d "$dir" ]] || {
        echo "Model directory not found: $dir" >&2
        return 1
    }

    if [[ -n "$quant" ]]; then
        candidate="$(find "$dir" -type f -name "*${quant}*.gguf" -print | LC_ALL=C sort | sed -n '1p')"
    else
        candidate="$(find "$dir" -type f -name '*.gguf' -print | LC_ALL=C sort | sed -n '1p')"
    fi

    if [[ -z "$candidate" ]]; then
        echo "No GGUF file matching quantization '${quant:-any}' found under $dir" >&2
        return 1
    fi

    local first_shard
    first_shard="$(find "$dir" -type f -name "*${quant}*00001-of-*.gguf" -print | LC_ALL=C sort | sed -n '1p')"
    if [[ -n "$first_shard" ]]; then
        candidate="$first_shard"
    elif [[ -n "$quant" ]]; then
        local matches
        matches="$(find "$dir" -type f -name "*${quant}*.gguf" -print | wc -l | tr -d ' ')"
        if [[ "$matches" -gt 1 ]]; then
            echo "Multiple GGUF files match '$quant'; pass a more specific quantization." >&2
            return 1
        fi
    fi

    printf '%s\n' "${candidate#"$MODEL_ROOT"/}"
}

list_models() {
    local alias label repo folder default_quant installed
    printf '%-26s %-31s %-12s %s\n' "ALIAS" "MODEL" "DEFAULT QUANT" "STATUS"
    while IFS='|' read -r alias label repo folder default_quant; do
        [[ -z "$alias" || "$alias" == \#* ]] && continue
        installed="not downloaded"
        if find "$MODEL_ROOT/$folder" -type f -name '*.gguf' -print -quit 2>/dev/null | grep -q .; then
            installed="downloaded"
        fi
        printf '%-26s %-31s %-12s %s\n' "$alias" "$label" "$default_quant" "$installed"
    done < "$CATALOG"
}

hf_cli() {
    if command -v hf >/dev/null 2>&1; then
        command -v hf
    elif [[ -x "$ROOT/.venv/bin/hf" ]]; then
        printf '%s\n' "$ROOT/.venv/bin/hf"
    else
        echo "Hugging Face CLI 'hf' is required. Install huggingface_hub with its CLI support." >&2
        return 1
    fi
}

download_repo() {
    local repo="$1" alias="$2" pattern="$3" marker_suffix="$4" marker hf plan
    local dest="$MODEL_ROOT/$alias"
    valid_alias "$alias" || {
        echo "Invalid local model alias: $alias" >&2
        return 2
    }
    [[ "$marker_suffix" =~ ^[a-zA-Z0-9._-]+$ ]] || {
        echo "Invalid download marker suffix: $marker_suffix" >&2
        return 2
    }
    marker="$dest/.download-complete-$marker_suffix"
    if [[ -f "$marker" ]] && find "$dest" -type f -name "$pattern" -print -quit | grep -q .; then
        echo "$alias ($marker_suffix) is already downloaded."
        return
    fi
    hf="$(hf_cli)"
    plan="$("$hf" download "$repo" --include "$pattern" --dry-run --format json)"
    if ! printf '%s\n' "$plan" | grep -q '"file"[[:space:]]*:'; then
        echo "No files in $repo match '$pattern'; verify the repository and quantization." >&2
        return 1
    fi
    mkdir -p "$dest"
    echo "Downloading $repo ($pattern) to $dest"
    "$hf" download "$repo" --include "$pattern" --local-dir "$dest"
    if ! find "$dest" -type f -name "$pattern" -print -quit | grep -q .; then
        echo "Hugging Face download completed without a matching GGUF file; check the repository and include pattern." >&2
        return 1
    fi
    touch "$marker"
}

download_catalog_model() {
    local spec="$1" fallback_quant="$2" alias quant entry label repo folder default_quant pattern
    alias="${spec%%:*}"
    if [[ "$spec" == *:* ]]; then
        quant="${spec#*:}"
    else
        quant="$fallback_quant"
    fi
    if ! entry="$(catalog_entry "$alias")"; then
        echo "Unknown catalog model: $alias" >&2
        echo "Use 'scripts/models.sh list' to see supported aliases, or use --repo/--name/--include." >&2
        return 2
    fi
    IFS='|' read -r label repo folder default_quant <<< "$entry"
    quant="${quant:-$default_quant}"
    [[ "$quant" =~ ^[a-zA-Z0-9._-]+$ ]] || {
        echo "Invalid quantization: $quant" >&2
        return 2
    }
    pattern="$(quant_pattern "$quant")"
    download_repo "$repo" "$folder" "$pattern" "$quant"
}

command="${1:-}"
if [[ -z "$command" ]]; then
    usage
    exit 2
fi
shift

case "$command" in
    list)
        list_models
        ;;
    resolve)
        [[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 2; }
        find_model_file "$1" "${2:-}"
        ;;
    download)
        models=""
        quant=""
        repo=""
        name=""
        include=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --model)
                    [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                    models="${models}${models:+ }$2"
                    shift 2
                    ;;
                --quant)
                    [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                    quant="$2"
                    shift 2
                    ;;
                --repo)
                    [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                    repo="$2"
                    shift 2
                    ;;
                --name)
                    [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                    name="$2"
                    shift 2
                    ;;
                --include)
                    [[ $# -ge 2 ]] || { usage >&2; exit 2; }
                    include="$2"
                    shift 2
                    ;;
                -h|--help)
                    usage
                    exit 0
                    ;;
                *)
                    echo "Unknown option: $1" >&2
                    usage >&2
                    exit 2
                    ;;
            esac
        done

        if [[ -n "$repo" || -n "$name" || -n "$include" ]]; then
            [[ -z "$models" && -n "$repo" && -n "$name" && -n "$include" ]] || {
                echo "Custom download requires --repo, --name, and --include, and cannot be mixed with --model." >&2
                exit 2
            }
            download_repo "$repo" "$name" "$include" "custom"
        else
            [[ -n "$models" ]] || { usage >&2; exit 2; }
            for spec in $models; do
                download_catalog_model "$spec" "$quant"
            done
        fi
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $command" >&2
        usage >&2
        exit 2
        ;;
esac
