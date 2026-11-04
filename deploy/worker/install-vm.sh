#!/bin/sh
# Run as root on a dedicated Ubuntu 24.04 VM after copying the reviewed bundle.
# Does not enable provider calls or start the worker.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
BUNDLE_DIR=${1:?Pass the uploaded bundle directory}
test -f "$BUNDLE_DIR/tandem-worker.service"
test -f "$BUNDLE_DIR/constraints.txt"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv ca-certificates
if ! id tandem >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/tandem --shell /usr/sbin/nologin tandem
fi
install -d -m 0700 -o tandem -g tandem /var/lib/tandem
install -d -m 0750 -o root -g tandem /etc/tandem
install -d -m 0755 -o root -g root /opt/tandem
python3 -m venv /opt/tandem/venv
/opt/tandem/venv/bin/pip install --disable-pip-version-check -c "$BUNDLE_DIR/constraints.txt" "$BUNDLE_DIR"/wheels/tandem_optimizer-*.whl "$BUNDLE_DIR"/wheels/tandem_worker-*.whl anthropic python-dotenv
/opt/tandem/venv/bin/python -c 'from tandem_worker.rules import decisions; from tandem_worker.serve import ServeConfig; assert decisions()["categories"]; assert ServeConfig().limits.actor_runs == 40'
install -m 0644 "$BUNDLE_DIR/tandem-worker.service" /etc/systemd/system/tandem-worker.service
systemctl daemon-reload
printf '%s\n' 'Packages installed. Worker remains disabled pending verified state migration.'
