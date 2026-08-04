# systemd deployment (PLAN.md D14, applied to audio-processing-service in D28)

Host-native deploy, no Docker (Docker stays local-dev/CI only, matching
record-service's `deploy/systemd/README.md`). One template unit, one
instance per CPU core -- scale by starting more instances, not by
threading inside one process.

**Simpler than record-service's deploy in one way**: instances don't need
individually-assigned ports or state directories. Every instance is just
another consumer in the same Redis Stream consumer group
(`audio-processing-workers` on `audio_derivative:stream`) -- Redis itself
distributes jobs across whichever instances are up, so there's no
load-balancing decision to make in front of this service (unlike
record-service's still-open "how does an agent pick which instance's gRPC
port to connect to" question).

## One-time host setup

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin audio-processing-service

sudo mkdir -p /opt/audio-processing-service /etc/audio-processing-service
sudo chown audio-processing-service:audio-processing-service /opt/audio-processing-service

# ffmpeg with libopus must be installed on the host -- this service shells
# out to the `ffmpeg` binary (src/audio_processing_service/infra/transcoder.py),
# it is NOT a Python dependency:
sudo apt-get install -y ffmpeg
ffmpeg -hide_banner -encoders 2>/dev/null | grep libopus   # sanity check

# Deploy the code (this repo's audio-ingestion/audio-processing-service/
# contents) to /opt/audio-processing-service, then build the venv the unit
# file's ExecStart points at:
cd /opt/audio-processing-service
sudo -u audio-processing-service python3 -m venv .venv
sudo -u audio-processing-service .venv/bin/pip install .
```

## Config

```bash
sudo cp deploy/systemd/common.env.example /etc/audio-processing-service/common.env
sudo $EDITOR /etc/audio-processing-service/common.env   # fill in Redis/MinIO/orchestrator secrets
sudo chown root:audio-processing-service /etc/audio-processing-service/common.env
sudo chmod 640 /etc/audio-processing-service/common.env
```

## Install + run

```bash
sudo cp deploy/systemd/audio-processing-service@.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now audio-processing-service@1
# sudo systemctl enable --now audio-processing-service@2   # once you want more throughput

systemctl status audio-processing-service@1
journalctl -u audio-processing-service@1 -f
```

The `%i` instance number only exists to let systemd manage multiple
independent processes under distinct unit names -- it isn't read by the
app itself (contrast with record-service's `%i`, which IS the gRPC port).
Any instance number works; `1`, `2`, `3`, ... is just a convention.

## Still open, out of scope for this template

- **CPUQuota/MemoryMax values in the unit file are placeholders**, not
  measured -- no benchmark script exists yet for this service (unlike
  record-service's `scripts/benchmark_concurrency.py`). Tune after
  observing real transcode CPU/duration in dev/prod.
- **Upgrades/restarts**: no rolling-restart tooling here. A plain
  `systemctl restart audio-processing-service@N` drops any in-flight
  ffmpeg job -- acceptable, since it isn't ack'd yet and Redis Streams'
  orphan-recovery (XAUTOCLAIM) will hand it to another instance (or the
  same one after restart) within `worker_timeout_sec` (default 30s).
