#!/usr/bin/env python3
"""Prepare a fixed Factory fixture mission, offline; never dispatch it."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from homelab.agent_requests import MAX_REQUEST_BYTES, RequestError, load_request, prepare_request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="Untrusted request JSON file")
    parser.add_argument("--output", required=True, help="New preparation JSON file; never overwrites")
    args = parser.parse_args(argv)
    try:
        with Path(args.request).open("rb") as source:
            request = load_request(source.read(MAX_REQUEST_BYTES + 1))
        prepared = prepare_request(request)
        encoded = (json.dumps(prepared, indent=2, sort_keys=True, ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
        # O_EXCL also refuses existing symlinks, including dangling symlinks.
        # The operator chooses the destination; no output path comes from JSON.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        print("Prepared only; no job submitted and no execution authorized.")
        return 0
    except (OSError, RequestError):
        print("Request preparation failed: invalid request, changed fixture, or unavailable new output.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
