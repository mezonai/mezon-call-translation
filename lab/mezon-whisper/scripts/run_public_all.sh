#!/usr/bin/env bash
# Everything that needs the GPU but no Mezon data, in one unattended run (plan 11.4):
#   RTF benchmark -> public WER baseline -> pilot LoRA -> convert -> WER of the pilot model.
# Start it inside tmux and leave; about 2-3 hours. Each step writes its own report/log,
# and a step that already produced its output is skipped, so the script can be re-run.
#
#   tmux new -s mezon
#   cd ~/mezon-whisper && source .venv/bin/activate && bash scripts/run_public_all.sh
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports logs

busy=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null)
if [ -n "$busy" ]; then
  echo "NOTE: other processes are on the GPU, RTF numbers will be pessimistic:"
  echo "$busy"
fi

nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used,power.draw --format=csv -l 10 \
  >> reports/gpu_public_all.csv &
logger=$!
trap 'kill $logger 2>/dev/null' EXIT

step() {  # step <name> <output that proves it is done> <command...>
  local name=$1 proof=$2; shift 2
  if [ -e "$proof" ]; then echo "== $name: skip ($proof exists)"; return 0; fi
  echo "== $name: start $(date '+%F %T')"
  if "$@" > "logs/$name.log" 2>&1; then
    echo "== $name: ok $(date '+%F %T')"
  else
    echo "== $name: FAILED, see logs/$name.log (last lines below); later steps still run"
    tail -n 15 "logs/$name.log"
  fi
}

TEST=(data/manifests/public_test_*.jsonl)
PILOT=(data/manifests/public_pilot_*.jsonl)

step 1_rtf reports/rtf_gpu_vs_cpu.md \
  python scripts/bench_rtf.py --manifest data/manifests/public_test_fleurs_vi_test.jsonl --minutes 30

step 2_public_baseline reports/public_baseline.md \
  python scripts/bench_rtf.py --minutes 0 --out reports/public_baseline.md \
    --configs gpu_fp16_beam1_batched gpu_fp16_beam5_batched --manifest "${TEST[@]}"

step 3_pilot_train models/runs/pilot/train_summary.json \
  python scripts/07_train.py --out models/runs/pilot --epochs 3 --eval-steps 100 --train "${PILOT[@]}"

step 4_pilot_convert models/pilot-ct2/model.bin \
  bash scripts/08_convert.sh models/runs/pilot/best models/pilot

step 5_pilot_eval reports/pilot_public.md \
  python scripts/bench_rtf.py --model models/pilot-ct2 --minutes 0 --out reports/pilot_public.md \
    --configs gpu_fp16_beam5_batched --manifest "${TEST[@]}"

echo
echo "Done $(date '+%F %T'). Send back: reports/*.md, models/runs/pilot/train_summary.json, and any logs/*.log that FAILED."
