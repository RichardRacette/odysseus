#!/usr/bin/env python3
"""Portable entry point; use --help. No installation required for core commands."""
from homelab.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
