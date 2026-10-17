"""Constrained SSH transport and remote deployment preflight."""

import re
import shlex
import subprocess

from .errors import SetupError


def ssh(host, script, data=None, timeout=180):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", host):
        raise SetupError("Use an SSH alias such as f50 or user@hostname")
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-T", host,
         "sh -c " + shlex.quote(script)],
        input=data,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise SetupError(f"SSH operation failed (exit {result.returncode})")


def remote_idle(base=None):
    """Return a shell preflight that refuses to touch an active installation."""
    return f"""
test "$(id -u)" = 0 || {{ echo 'Root SSH is required' >&2; exit 1; }}
mkdir -p "$base/run"
chmod 700 "$base" "$base/run"
exec 9>"$base/run/control.lock"
flock -n 9 9>&9 || {{ echo 'Another proxy control operation is running' >&2; exit 1; }}
if [ -e "$base/run/owned" ]; then
  echo 'Existing proxy rules are owned; run f50-proxy stop before deployment' >&2
  exit 1
fi
if [ -f "$base/run/core.pid" ]; then
  read -r core_pid < "$base/run/core.pid"
  case "$core_pid" in ''|*[!0-9]*) echo 'Invalid existing PID file; inspect before deployment' >&2; exit 1;; esac
  if kill -0 "$core_pid" 2>/dev/null; then
    echo 'Core is running; run f50-proxy stop before deployment' >&2
    exit 1
  fi
fi
"""

