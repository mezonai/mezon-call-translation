#!/bin/bash
# Setup centralized logging via rsyslog and logrotate
# Supports: Debian/Ubuntu and CentOS 8
# Must be run with sudo

set -e

if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root (sudo ./scripts/setup-logging.sh)"
  exit 1
fi

# Detect OS
if [ -f /etc/debian_version ]; then
  OS_FAMILY="debian"
  PKG_MANAGER="apt-get"
  RSYSLOG_USER="syslog"
  LOG_GROUP="adm"
elif [ -f /etc/redhat-release ] || [ -f /etc/centos-release ]; then
  OS_FAMILY="redhat"
  PKG_MANAGER="dnf"
  RSYSLOG_USER="root"
  LOG_GROUP="root"
else
  echo "ERROR: Unsupported OS. This script supports Debian/Ubuntu and CentOS/RHEL only."
  echo "       Please install rsyslog and logrotate manually, then re-run."
  exit 1
fi

echo "Detected OS family: $OS_FAMILY (using $PKG_MANAGER)"

# Install rsyslog and logrotate if not present
echo "Checking required packages (rsyslog, logrotate)..."

if [ "$OS_FAMILY" = "debian" ]; then
  apt-get update -qq
fi

for pkg in rsyslog logrotate; do
  if [ "$OS_FAMILY" = "debian" ]; then
    installed=$(dpkg -l "$pkg" 2>/dev/null | grep -c "^ii" || true)
    is_installed=$([ "$installed" -gt 0 ] && echo yes || echo no)
  else
    is_installed=$(rpm -q "$pkg" &>/dev/null && echo yes || echo no)
  fi

  if [ "$is_installed" = "yes" ]; then
    echo "  ✔ $pkg is already installed."
  else
    echo "  Installing $pkg..."
    $PKG_MANAGER install -y "$pkg"
    echo "  ✔ $pkg installed."
  fi
done

LOG_DIR="/var/log/mezon-call-translation"

# Apply SELinux context on CentOS/RHEL if SELinux is active
if [ "$OS_FAMILY" = "redhat" ]; then
  if command -v getenforce &>/dev/null && [ "$(getenforce)" != "Disabled" ]; then
    echo "SELinux is active — applying var_log_t context to $LOG_DIR..."
    if ! command -v semanage &>/dev/null; then
      echo "  Installing policycoreutils-python-utils for semanage..."
      dnf install -y policycoreutils-python-utils
    fi
    semanage fcontext -a -t var_log_t "$LOG_DIR(/.*)?" 2>/dev/null || \
      semanage fcontext -m -t var_log_t "$LOG_DIR(/.*)?"`
    restorecon -Rv "$LOG_DIR"
    echo "  ✔ SELinux context set to var_log_t on $LOG_DIR"
  else
    echo "SELinux is disabled — skipping SELinux context step."
  fi
fi

# Create log directory
echo "Creating central log directory: $LOG_DIR"
mkdir -p "$LOG_DIR"
chown "$RSYSLOG_USER:$LOG_GROUP" "$LOG_DIR"
chmod 755 "$LOG_DIR"

# Write rsyslog routing rules
echo "Generating rsyslog configuration..."
cat <<'RSYSLOG_CONF' > /etc/rsyslog.d/mezon-call-translation.conf
if $programname == 'stt_service' then /var/log/mezon-call-translation/stt_service.log
& stop

if $programname == 'stt_metrics' then /var/log/mezon-call-translation/metrics.log
& stop

if $programname == 'orchestrator_service' then /var/log/mezon-call-translation/orchestrator_service.log
& stop

if $programname == 'agents_service' then /var/log/mezon-call-translation/agents_service.log
& stop

if $programname == 'tts_service' then /var/log/mezon-call-translation/tts_service.log
& stop
RSYSLOG_CONF

# Write logrotate rules
echo "Generating logrotate configuration..."
cat <<LOGROTATE_CONF > /etc/logrotate.d/mezon-call-translation
/var/log/mezon-call-translation/*.log {
    su $RSYSLOG_USER $LOG_GROUP
    size 500M
    rotate 999
    compress
    delaycompress
    dateext
    dateformat -%Y-%m-%d
    missingok
    notifempty
    create 0644 $RSYSLOG_USER $LOG_GROUP
}
LOGROTATE_CONF

# Restart rsyslog to apply new configuration
echo "Restarting rsyslog service..."
if command -v systemctl &>/dev/null && systemctl is-system-running &>/dev/null; then
  systemctl restart rsyslog
elif command -v service &>/dev/null; then
  service rsyslog restart
else
  echo "WARNING: Could not restart rsyslog automatically. Please restart it manually."
fi

echo ""
echo "✔ Logging setup complete! Logs will be stored in $LOG_DIR."
echo "  OS: $OS_FAMILY | Log owner: $RSYSLOG_USER:$LOG_GROUP"