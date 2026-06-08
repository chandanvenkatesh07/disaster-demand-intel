#!/usr/bin/env bash
# Run aider against the local-LLM gateway via the dev proxy.
# Usage: scripts/_aider.sh <files...> -- "<task message>"
# The "--" separator splits files from the message.

set -euo pipefail
cd "$(dirname "$0")/.."

files=()
while [[ $# -gt 0 && "$1" != "--" ]]; do
  files+=("$1"); shift
done
[[ "${1:-}" == "--" ]] && shift
msg="${1:-}"
[[ -z "$msg" ]] && { echo "missing task message"; exit 2; }

file_args=()
for f in "${files[@]}"; do file_args+=("--file" "$f"); done

OPENAI_API_KEY="sk-proxy-passthrough" \
.venv/bin/aider \
  --openai-api-base http://127.0.0.1:8765/v1 \
  --model "openai/Qwen3.6-35B-A3B-oQ6-mtp" \
  --weak-model "openai/Qwen3.6-35B-A3B-oQ6-mtp" \
  --edit-format whole \
  --no-auto-commits \
  --no-auto-test \
  --no-show-model-warnings \
  --no-gitignore \
  --no-detect-urls \
  --no-stream \
  --yes-always \
  --map-tokens 0 \
  "${file_args[@]}" \
  --message "$msg"
