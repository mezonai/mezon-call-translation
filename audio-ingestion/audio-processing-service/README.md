# audio-processing-service (retired)

This directory contains the former PCM-to-Ogg derivative worker. It is kept
only as historical code and is not part of the active recording pipeline.

The active flow is now:

```text
SFU Opus RTP
  -> agent wraps the existing Opus packets in Ogg
  -> record-service stores that original .ogg object in MinIO
  -> record-service reports recording.completed
  -> orchestrator stores the original object key in tracks.audio_info
  -> get_audio_info returns that original key
```

Consequently, orchestrator no longer publishes `audio_derivative:stream`
tasks, accepts `derivative.completed`/`derivative.failed` events, or tracks a
`derivative_status`. Starting this worker would leave it idle unless an
external legacy producer still writes jobs to its Redis stream.

Do not deploy this service for the current recording architecture. Its source,
tests, and systemd examples remain solely for reference while the retired
component is being removed from repository history.
