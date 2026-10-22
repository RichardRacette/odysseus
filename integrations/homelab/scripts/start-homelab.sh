#!/bin/sh
# Foreground launcher; --install-startup explicitly creates a macOS user LaunchAgent.
set -eu

usage() {
    cat <<'USAGE'
Usage: sh start-homelab.sh --config FILE --python EXECUTABLE [--role hub|worker]
       sh start-homelab.sh --config FILE --python EXECUTABLE --install-startup [--label NAME]

Default: foreground run. --install-startup is macOS-only and creates a new
current-user LaunchAgent; it never replaces an existing file/job. A user login
is required. This does not configure a boot daemon, networking, or backups.
USAGE
}

homelab_config=''
homelab_python=''
homelab_role='hub'
homelab_install='no'
homelab_label=''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --config|--python|--role|--label)
            if [ "$#" -lt 2 ]; then printf '%s\n' 'Missing option value.' >&2; exit 2; fi
            case "$1" in
                --config) homelab_config=$2 ;;
                --python) homelab_python=$2 ;;
                --role) homelab_role=$2 ;;
                --label) homelab_label=$2 ;;
            esac
            shift 2 ;;
        --install-startup) homelab_install='yes'; shift ;;
        *) printf '%s\n' 'Unknown option. Use --help.' >&2; exit 2 ;;
    esac
done
case "$homelab_role" in
    hub) homelab_command='serve' ;;
    worker) homelab_command='worker' ;;
    *) printf '%s\n' 'Role must be hub or worker.' >&2; exit 2 ;;
esac
if [ -z "$homelab_config" ] || [ -z "$homelab_python" ] || [ ! -f "$homelab_config" ]; then
    printf '%s\n' 'Supply an existing config file and a Python executable. Use --help.' >&2
    exit 2
fi
case "$homelab_python" in -*) printf '%s\n' 'Invalid Python executable.' >&2; exit 2 ;; esac
homelab_python=$(command -v "$homelab_python") || { printf '%s\n' 'Python executable not found.' >&2; exit 2; }
homelab_root=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd -P)
if [ ! -f "$homelab_root/lab.py" ]; then printf '%s\n' 'lab.py is missing.' >&2; exit 2; fi
# Preserve symlinks for the application's policy check, and retain a chosen venv.
homelab_config=$("$homelab_python" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).absolute())' "$homelab_config")
homelab_python=$("$homelab_python" -c 'from pathlib import Path; import sys; print(Path(sys.executable).absolute())')

if [ "$homelab_install" = 'yes' ]; then
    if [ "$(uname -s)" != 'Darwin' ]; then
        printf '%s\n' 'Startup installation here is macOS-only. Foreground mode works on POSIX systems.' >&2
        exit 2
    fi
    if [ -z "$homelab_label" ]; then homelab_label="com.odysseus.homelab.$homelab_role"; fi
    homelab_plist=$("$homelab_python" - "$homelab_root" "$homelab_config" "$homelab_python" "$homelab_command" "$homelab_label" <<'PY'
import os
from pathlib import Path
import plistlib
import re
import sys
import tempfile

root, config, python, command, label = sys.argv[1:]
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", label):
    raise SystemExit("Invalid LaunchAgent label")
directory = Path.home() / "Library" / "LaunchAgents"
directory.mkdir(parents=True, exist_ok=True)
target = directory / (label + ".plist")
if target.exists() or target.is_symlink():
    raise SystemExit("LaunchAgent already exists; no file or job was replaced")
settings = {
    "Label": label,
    "ProgramArguments": [python, str(Path(root) / "lab.py"), command, "--config", config],
    "WorkingDirectory": root,
    "RunAtLoad": True,
    "KeepAlive": {"SuccessfulExit": False},
    "ThrottleInterval": 60,
    "ProcessType": "Background",
}
fd, temporary = tempfile.mkstemp(prefix=".homelab-", dir=directory)
try:
    with os.fdopen(fd, "wb") as handle:
        plistlib.dump(settings, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.link(temporary, target)  # Exclusive publication: never replace an existing file.
finally:
    os.unlink(temporary)
print(target)
PY
)
    if ! launchctl bootstrap "gui/$(id -u)" "$homelab_plist"; then
        printf '%s\n' 'LaunchAgent file was created but bootstrap failed. Inspect it locally; no existing job was replaced.' >&2
        exit 1
    fi
    printf '%s\n' "Installed user LaunchAgent '$homelab_label'. Verify its status and your next login."
    exit 0
fi

exec "$homelab_python" "$homelab_root/lab.py" "$homelab_command" --config "$homelab_config"
