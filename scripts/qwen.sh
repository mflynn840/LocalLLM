#!/bin/bash
source .venv/bin/activate
set -e

MODEL_DIR="$HOME/AI/models/qwen3-coder"
LLAMA="$HOME/AI/llama.cpp/build/bin/llama-server"

HF_REPO="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF"
MODEL_PREFIX="Qwen3-Coder-30B-A3B-Instruct"

mkdir -p "$MODEL_DIR"

GREEN='\033[0;32m'
RESET='\033[0m'

MODEL_FILE_Q3_S="${MODEL_PREFIX}-Q3_K_S.gguf"
MODEL_FILE_Q3_M="${MODEL_PREFIX}-Q3_K_M.gguf"
MODEL_FILE_Q4_S="${MODEL_PREFIX}-Q4_K_S.gguf"
MODEL_FILE_Q4_M="${MODEL_PREFIX}-Q4_K_M.gguf"
MODEL_FILE_Q5_S="${MODEL_PREFIX}-Q5_K_S.gguf"
MODEL_FILE_Q5_M="${MODEL_PREFIX}-Q5_K_M.gguf"

installed_marker() {
    if [ -f "$MODEL_DIR/$1" ]; then
        echo -e " ${GREEN}✓ INSTALLED${RESET}"
    else
        echo ""
    fi
}

echo
echo "Qwen3-Coder 30B-A3B"
echo "=================="
echo

echo -n "1) Q3_K_S  (~13.3 GB)"
installed_marker "$MODEL_FILE_Q3_S"

echo -n "2) Q3_K_M  (~14.7 GB)  [recommended for 16 GB VRAM]"
installed_marker "$MODEL_FILE_Q3_M"

echo -n "3) Q4_K_S  (~17.5 GB)  [will not fully fit]"
installed_marker "$MODEL_FILE_Q4_S"

echo -n "4) Q4_K_M  (~18.6 GB)  [will not fully fit]"
installed_marker "$MODEL_FILE_Q4_M"

echo -n "5) Q5_K_S  (~21.1 GB)  [will not fully fit]"
installed_marker "$MODEL_FILE_Q5_S"

echo -n "6) Q5_K_M  (~21.7 GB)  [will not fully fit]"
installed_marker "$MODEL_FILE_Q5_M"

echo

read -rp "Select quantization [1-6]: " choice

case "$choice" in
    1) QUANT="Q3_K_S" ;;
    2) QUANT="Q3_K_M" ;;
    3) QUANT="Q4_K_S" ;;
    4) QUANT="Q4_K_M" ;;
    5) QUANT="Q5_K_S" ;;
    6) QUANT="Q5_K_M" ;;
    *)
        echo "Invalid selection."
        exit 1
        ;;
esac

MODEL_FILE="${MODEL_PREFIX}-${QUANT}.gguf"
MODEL_PATH="$MODEL_DIR/$MODEL_FILE"

echo
echo "Selected:"
echo "  $QUANT"
echo "  $MODEL_FILE"
echo

if [ ! -f "$MODEL_PATH" ]; then
    echo "Model not found locally."
    echo "Downloading from Hugging Face..."
    echo

    hf download \
        "$HF_REPO" \
        "$MODEL_FILE" \
        --local-dir "$MODEL_DIR"
else
    echo -e "${GREEN}✓ Model already downloaded.${RESET}"
fi

echo
echo "Model:"
ls -lh "$MODEL_PATH"
echo

echo "Starting llama-server..."
echo

exec "$LLAMA" \
    -m "$MODEL_PATH" \
    --alias "qwen3-coder-30b-a3b" \
    -ngl 999 \
    -fa on \
    --parallel 1 \
    --ctx-size 8192 \
    --batch-size 256 \
    --host 0.0.0.0 \
    --port 8080