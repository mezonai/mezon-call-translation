# systemd deployment

Host-native deploy, no Docker (same policy as
`audio-ingestion/record-service`, PLAN.md D14 -- Docker stays local-dev/CI
only). Unlike record-service's N-instance-per-core template, this is a
**single, non-templated unit**: `worker-manager` is the only long-lived
process. It does the NATS subscribe and, per room, `exec.Command`s a plain
`agent` subprocess itself (internal/workermanager/manager.go) -- there is no
separate systemd unit per agent, and no unit per room. systemd's job here is
just "keep worker-manager itself running"; worker-manager's own code is what
keeps agents running across its own restarts (see the unit file's KillMode
comment -- read that one before touching this setup) and, since
2026-08-28, can reconnect to and manage agents left over from *before* a
restart too (Reconcile, see the unit file's StateDirectory/RuntimeDirectory
comment) -- no manual step needed for that, systemd creates both
directories itself on first start from the unit file's declarations.

## One-time host setup

```bash
sudo mkdir -p /opt/mezon-agents/bin /etc/mezon-agents
cd agents
go build -o bin/agent ./cmd/agent
go build -o bin/worker-manager ./cmd/worker-manager
sudo install -o changeme -g changeme -m 755 bin/agent bin/worker-manager /opt/mezon-agents/bin/
```

## Config

```bash
sudo cp deploy/systemd/worker-manager.env.example /etc/mezon-agents/worker-manager.env
sudo $EDITOR /etc/mezon-agents/worker-manager.env   # fill in NATS/SFU/orchestrator secrets
sudo chown root:changeme /etc/mezon-agents/worker-manager.env
sudo chmod 640 /etc/mezon-agents/worker-manager.env
```

One file covers both worker-manager's own settings and everything it
forwards into each spawned agent's env -- see the file's own header comment
for why there's no separate per-agent env file. If
`internal/workermanager/config.go`'s `agentPassthroughEnvKeys` list ever
gains a new var, add it here too or the corresponding agent-side setting
silently falls back to its compiled-in default instead of what's in this
file.

## Install + run

```bash
sudo cp deploy/systemd/mezon-agents-worker-manager.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now mezon-agents-worker-manager

systemctl status mezon-agents-worker-manager
journalctl -u mezon-agents-worker-manager -f    # interleaves worker-manager's own logs
                                                # with every spawned agent's stdout/stderr
                                                # (manager.go wires them to the same
                                                # streams) -- filter by room_id/pid in the
                                                # log lines themselves, not by unit.
```
