#!/usr/bin/env python3
"""Explicit operator entry point for the opt-in durable Factory coordinator."""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import threading

from homelab.config import ConfigError, load_config
from homelab.dispatch_receipts import ReceiptError, read_json, verify_receipt
from homelab.durable_dispatch import DispatchError, Dispatcher
from homelab.factory import FactoryError
from homelab.queue import QueueError


def parser():
    p = argparse.ArgumentParser(description="Advance explicitly authorized fixed-output Factory missions")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ["admit", "pause", "status", "run"]:
        c = sub.add_parser(name)
        c.add_argument("--config", required=True, help="Existing private hub configuration")
        c.add_argument("--receipts", required=True, help="Private receipt directory outside the checkout")
        if name == "admit":
            c.add_argument("--mission", required=True)
            c.add_argument("--authorization-ref", required=True, help="Reference to the owner's scoped execution authorization")
        if name == "pause":
            c.add_argument("--mission-id", required=True)
        if name == "run":
            c.add_argument("--once", action="store_true")
            c.add_argument("--interval", type=float, default=2)
            c.add_argument("--max-seconds", type=float)
    v = sub.add_parser("verify")
    v.add_argument("--receipt", required=True)
    v.add_argument("--evidence", required=True)
    v.add_argument("--mission", required=True, help="Separately trusted manifest; never inferred from the receipt")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "verify":
            result = verify_receipt(args.receipt, args.evidence, read_json(args.mission))
        else:
            dispatcher = Dispatcher(load_config(args.config), args.receipts)
            if args.command == "admit":
                result = dispatcher.admit(read_json(args.mission), args.authorization_ref)
            elif args.command == "pause":
                result = dispatcher.pause(args.mission_id)
            elif args.command == "status":
                result = {"missions": dispatcher.status()}
            elif args.once:
                result = dispatcher.tick()
            else:
                stop = threading.Event()
                signal.signal(signal.SIGINT, lambda *_: stop.set())
                signal.signal(signal.SIGTERM, lambda *_: stop.set())
                previous = None

                def changed(value):
                    nonlocal previous
                    line = json.dumps(value, sort_keys=True)
                    if line != previous:
                        print(line, flush=True)
                        previous = line

                result = dispatcher.run(interval=args.interval, max_seconds=args.max_seconds,
                                        stop_event=stop, on_tick=changed)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (ConfigError, FactoryError, QueueError, DispatchError, ReceiptError,
            OSError, sqlite3.Error, ValueError):
        # Never echo manifests, credentials, results, or exception paths to service logs.
        print("Dispatcher operation failed; check configuration, interval, authorization, scope and receipt integrity.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
