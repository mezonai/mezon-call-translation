#!/usr/bin/env bash
# Dev-time only. recording_pb2.py / recording_pb2_grpc.py are COMMITTED
# source (not generated at deploy time) -- run this by hand whenever
# src/proto/recording.proto changes, then `git add` the output alongside the
# .proto change in the same commit.
# Keep proto/recording.proto in sync with
# ../../../audio-ingestion/record-service/proto/recording.proto by hand --
# see the comment at the top of that file for why they aren't shared.
# Requires grpcio-tools (pip install grpcio-tools; not a runtime dependency,
# not in requirements-agent.txt).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_DIR="src/proto"

python -m grpc_tools.protoc \
    -I src/proto \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    src/proto/recording.proto

sed -i \
    's/^import recording_pb2 as recording__pb2/from . import recording_pb2 as recording__pb2/' \
    "$OUT_DIR/recording_pb2_grpc.py"

echo "Generated $OUT_DIR/recording_pb2.py and recording_pb2_grpc.py"
