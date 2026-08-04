#!/usr/bin/env bash
# Dev-time only. recording_pb2.py / recording_pb2_grpc.py are COMMITTED
# source (not built at deploy/Docker-build time, see PLAN.md) -- run this by
# hand whenever proto/recording.proto changes, then `git add` the output
# alongside the .proto change in the same commit.
# Requires: pip install -e .[dev] (or any env with grpcio-tools).
set -euo pipefail
cd "$(dirname "$0")/.."

OUT_DIR="src/record_service/infra/grpc"

python -m grpc_tools.protoc \
    -I proto \
    --python_out="$OUT_DIR" \
    --grpc_python_out="$OUT_DIR" \
    proto/recording.proto

# protoc's default codegen emits a flat `import recording_pb2`, which breaks
# once these files live inside the record_service.infra.grpc package -- patch
# it to a relative import.
sed -i \
    's/^import recording_pb2 as recording__pb2/from . import recording_pb2 as recording__pb2/' \
    "$OUT_DIR/recording_pb2_grpc.py"

echo "Generated $OUT_DIR/recording_pb2.py and recording_pb2_grpc.py -- remember to commit them"
