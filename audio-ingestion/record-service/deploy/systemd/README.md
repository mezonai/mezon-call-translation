# systemd deployment (PLAN.md D14)

Host-native deploy, no Docker (Docker stays local-dev/CI only). One
template unit, one instance per CPU core (PLAN.md D13's "1 process ~= 1
core" cost model) -- scale by starting more instances, not by threading
inside one process.

## One-time host setup

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin record-service

sudo mkdir -p /opt/record-service /etc/record-service
sudo chown record-service:record-service /opt/record-service

# Deploy the code (this repo's audio-ingestion/record-service/ contents)
# to /opt/record-service, then build the venv the unit file's ExecStart
# points at:
cd /opt/record-service
sudo -u record-service python3 -m venv .venv
sudo -u record-service .venv/bin/pip install .
```

## Config

```bash
sudo cp deploy/systemd/common.env.example /etc/record-service/common.env
sudo $EDITOR /etc/record-service/common.env     # fill in MinIO/orchestrator secrets
sudo chown root:record-service /etc/record-service/common.env
sudo chmod 640 /etc/record-service/common.env

# One of these per instance you plan to run (N = core count from the
# benchmark, PLAN.md D13 action item -- not decided yet, start with N=1
# and adjust once that's measured):
sudo cp deploy/systemd/instance.env.example /etc/record-service/instance-1.env
sudo $EDITOR /etc/record-service/instance-1.env   # port=50051, state dir=.../1
# repeat for instance-2.env (port=50052, state dir=.../2), etc.
```

## Install + run

```bash
sudo cp deploy/systemd/record-service@.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now record-service@1
# sudo systemctl enable --now record-service@2   # once you have more instances

systemctl status record-service@1
journalctl -u record-service@1 -f
```

## Still open, out of scope for this template

- **Load balancing across instances**: nothing here decides how an agent
  picks which instance's port to connect to when N > 1. Needs a decision
  (nginx stream proxy, a gRPC-native LB, DNS round-robin, sticky-by-room
  hashing, ...) before running more than 1 instance for real. Not
  record-service's concern either way -- it has no opinion about what's in
  front of it.
- **CPUQuota/MemoryMax values in the unit file are placeholders**, not
  measured -- see PLAN.md D13's benchmark action item.
- **Upgrades/restarts**: no rolling-restart tooling here. A plain
  `systemctl restart record-service@N` will drop any live sessions on that
  instance into the abrupt-disconnect path (PLAN.md D5 tier 2, 45s grace
  then best-effort finalize) -- acceptable for now per the same reasoning
  as D5, revisit if that stops being true.
