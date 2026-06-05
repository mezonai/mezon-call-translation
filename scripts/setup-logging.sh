#!/bin/bash
set -e

# Script to configure rsyslog and logrotate for Mezon services.
# MUST BE RUN WITH SUDO.

if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (sudo)."
  exit 1
fi

LOG_DIR="/var/log/mezon-call-translation"

echo "Creating central log directory: $LOG_DIR"
mkdir -p "$LOG_DIR"

echo "Setting permissions for log directory..."
chown syslog:adm "$LOG_DIR"
chmod 755 "$LOG_DIR"

echo "Generating rsyslog configuration..."
cat <<EOF > /etc/rsyslog.d/mezon-call-translation.conf
if \$programname == 'stt_service' then $LOG_DIR/stt_service.log
& stop

if \$programname == 'stt_metrics' then $LOG_DIR/metrics.log
& stop

if \$programname == 'orchestrator_service' then $LOG_DIR/orchestrator_service.log
& stop

if \$programname == 'agents_service' then $LOG_DIR/agents_service.log
& stop

if \$programname == 'tts_service' then $LOG_DIR/tts_service.log
& stop
EOF

echo "Generating logrotate configuration..."
cat <<EOF > /etc/logrotate.d/mezon-call-translation
$LOG_DIR/*.log {
    su syslog adm
    size 500M
    rotate 5
    compress
    delaycompress
    missingok
    notifempty
    create 0644 syslog adm
}
EOF

echo "Restarting rsyslog service..."
systemctl restart rsyslog

echo "Logging setup complete! Logs will be stored in $LOG_DIR."
