# audio-ingestion

The active audio-ingestion path stores client-playable Ogg/Opus directly.

## Current flow

```text
mezon-sfu RTP/Opus
  → Go agent audiopipeline
      - muxes existing Opus into Ogg without re-encoding speech
      - encodes valid Opus silence for timeline gaps
      - starts a chained logical Ogg stream on SFU binding/SSRC changes
  → record-service gRPC StreamAudio
      - accepts Ogg fragments
      - multipart-uploads one original .ogg object to MinIO/S3
      - reports recording.started/completed/failed
  → orchestrator_service
      - stores the original object key in Track.audio_info
      - queues the same Ogg object for batch STT
      - exposes the original key through get_audio_info
```

There is no derivative conversion step in the active pipeline. The
`audio-processing-service/` directory is retained only as historical code and
is not dispatched by orchestrator.

See [`record-service/README.md`](record-service/README.md) for the ingest,
recovery, storage, and deployment contracts.
