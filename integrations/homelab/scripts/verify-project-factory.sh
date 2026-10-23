#!/usr/bin/env bash
# One-shot disposable acceptance drill. No setup, deployment or configuration mutation.
set -euo pipefail
umask 077
if [ "$#" -ne 1 ]; then
  printf '%s\n' 'Usage: bash scripts/verify-project-factory.sh /absolute/new/private/output' >&2
  exit 2
fi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir/.."
if command -v python3 >/dev/null 2>&1; then
  python_cmd=python3
elif command -v python >/dev/null 2>&1; then
  python_cmd=python
else
  echo 'Python 3.10+ is required' >&2
  exit 2
fi
"$python_cmd" -m unittest discover -s tests -v
"$python_cmd" -m compileall -q homelab lab.py factory.py
"$python_cmd" factory.py demo --output "$1"
"$python_cmd" factory.py verify-evidence --file "$1/EVIDENCE.json"
printf '%s\n' 'Local disposable Project Factory acceptance passed; physical-host and GitHub security gates remain separate.'
