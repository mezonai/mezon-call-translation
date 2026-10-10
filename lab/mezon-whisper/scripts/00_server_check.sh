#!/usr/bin/env bash
# Inventory of the GPU server (plan section 9.1). Read-only: installs nothing.
# Usage: bash scripts/00_server_check.sh | tee reports/server_check.txt
set -u

section() { printf '\n===== %s =====\n' "$1"; }

section "OS / kernel"
uname -a
cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)='

section "GPU / driver (Blackwell sm_120 needs driver >= 570, CUDA >= 12.8)"
nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv 2>&1
nvidia-smi 2>&1 | grep -E 'CUDA Version' || true
command -v nvcc >/dev/null && nvcc --version | tail -1 || echo "nvcc: not installed (OK, torch wheels ship their own CUDA runtime)"

section "Other processes on the GPU (is anyone else using it?)"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>&1

section "CPU / RAM"
nproc
free -h

section "Disk (need >= 200-300GB free for data + models)"
df -h / "$HOME" /data /mnt 2>/dev/null

section "Python / tools"
for bin in python3 python3.12 python3.11 uv pip3 ffmpeg tmux screen docker git; do
  printf '%-10s ' "$bin"; command -v "$bin" >/dev/null && "$bin" --version 2>&1 | head -1 || echo "missing"
done
ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libopus && echo "ffmpeg libopus: yes" || echo "ffmpeg libopus: NO"

section "Network: HuggingFace / PyPI / PyTorch index"
for url in https://huggingface.co https://pypi.org https://download.pytorch.org; do
  printf '%-32s ' "$url"; curl -s -o /dev/null -m 10 -w '%{http_code} %{time_total}s\n' "$url" || echo "unreachable"
done

section "Network: internal services (set MINIO_HOST / PG_HOST before running)"
for hp in "${MINIO_HOST:-}" "${PG_HOST:-}"; do
  [ -z "$hp" ] && continue
  host=${hp%:*}; port=${hp##*:}
  printf '%-32s ' "$hp"; timeout 5 bash -c "</dev/tcp/$host/$port" 2>/dev/null && echo "open" || echo "UNREACHABLE"
done
[ -z "${MINIO_HOST:-}${PG_HOST:-}" ] && echo "skipped (e.g. MINIO_HOST=10.0.0.5:9000 PG_HOST=10.0.0.6:5432)"
