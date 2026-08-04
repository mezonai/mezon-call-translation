FROM python:3.12-slim AS base

# v1 ghi raw PCM, không encode (D6) -> không cần ffmpeg trong image này,
# khác với audio-processing-service sẽ cần ffmpeg cho việc transcode.

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY proto ./proto

# recording_pb2.py / recording_pb2_grpc.py are committed source (see
# src/record_service/infra/grpc/), not generated at build time -- this image
# only needs the grpcio runtime, not grpcio-tools/protoc. Run
# scripts/gen_proto.sh by hand and commit the result after changing
# proto/recording.proto.
RUN pip install --no-cache-dir .

ENV RECORD_STATE_DIR=/data/record-service/state
RUN mkdir -p /data/record-service/state

EXPOSE 50051

CMD ["python", "-m", "record_service.main"]
