"""Explicit local recovery, diagnostics, and pull-only Git mirrors.

Snapshots contain credentials and indexed source text. They are private, unencrypted
exports intended for an encrypted backup tool. No function prints file contents.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


_FORMAT = "odysseus-homelab-snapshot"
_DATABASES = {"queue.sqlite3", "knowledge.sqlite3"}
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_MIRROR_MARKER = "homelab-mirror.json"


class OperationsError(ValueError):
    """A safe, user-facing failure without subprocess output or secret values."""


def _checked_path(value) -> Path:
    try:
        path = Path(os.path.abspath(os.fspath(value)))
    except (TypeError, ValueError, OSError):
        raise OperationsError("Invalid filesystem path") from None
    _reject_links(path)
    return path


def _reject_links(path: Path) -> None:
    # Reparse points include Windows junctions, not just Python symbolic links.
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OperationsError("Symbolic links and reparse points are not allowed")


def _overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _destination(source: Path, destination, *, allow_empty=False) -> Path:
    dest = _checked_path(destination)
    if _overlap(source, dest):
        raise OperationsError("Source and destination must not overlap")
    if not dest.parent.is_dir():
        raise OperationsError("Destination parent directory must already exist")
    if dest.exists() and not (allow_empty and dest.is_dir() and not any(dest.iterdir())):
        raise OperationsError("Destination already exists and cannot be overwritten")
    return dest


def _mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _write_bytes(path: Path, contents: bytes) -> None:
    _mkdir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(contents)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: dict) -> None:
    _write_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _read_json(path: Path, maximum=4 * 1024 * 1024) -> dict:
    _reject_links(path)
    try:
        if path.stat().st_size > maximum:
            raise OperationsError("JSON state file exceeds the allowed size")
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise OperationsError("JSON state file must contain an object")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise OperationsError("Cannot read a valid JSON state file") from None


def _kind(relative: str) -> str | None:
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        return None
    parts = PurePosixPath(relative).parts
    if not parts or any(not _SAFE_NAME.fullmatch(part) for part in parts):
        return None
    if relative in _DATABASES:
        return "sqlite"
    if relative == "config.json":
        return "config"
    if len(parts) == 2 and parts[0] == "secrets" and parts[1].endswith(".token"):
        return "secret"
    if parts[0] == "workers":
        if len(parts) == 2 and parts[1].endswith(".json"):
            return "config"
        if len(parts) == 3 and parts[2] in {"worker.json", "config.json"}:
            return "config"
    return None


def _state_files(state: Path) -> list[tuple[str, Path]]:
    files = []
    candidates = [state / name for name in sorted(_DATABASES | {"config.json"})]
    for dirname in ("secrets", "workers"):
        directory = state / dirname
        _reject_links(directory)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise OperationsError("State subdirectory is not a directory")
        for child in sorted(directory.iterdir()):
            _reject_links(child)
            candidates.append(child)
            if dirname == "workers" and child.is_dir() and _SAFE_NAME.fullmatch(child.name):
                for name in ("worker.json", "config.json"):
                    candidates.append(child / name)
    for path in candidates:
        _reject_links(path)
        relative = path.relative_to(state).as_posix()
        if _kind(relative) is None or not path.exists():
            continue
        if not path.is_file():
            raise OperationsError("A recognized state file is not a regular file")
        if _kind(relative) == "secret" and path.stat().st_size > 8192:
            raise OperationsError("Credential file exceeds the allowed size")
        files.append((relative, path))
    if not files:
        raise OperationsError("No recognized state files found")
    if len(files) > 1024:
        raise OperationsError("Too many state files for one snapshot")
    return sorted(files)


def _credential_fields(config: dict):
    if "admin_token_file" in config:
        yield config, "admin_token_file"
    for group in ("nodes", "readers"):
        entries = config.get(group, {})
        if not isinstance(entries, dict):
            raise OperationsError("Credential configuration must contain objects")
        for entry in entries.values():
            if not isinstance(entry, dict) or "token_file" not in entry:
                raise OperationsError("Credential configuration is incomplete")
            yield entry, "token_file"


def _validate_state_credentials(state: Path, sources: list[tuple[str, Path]]) -> None:
    if not (state / "config.json").exists():
        return
    config = _read_json(state / "config.json")
    selected = {path for _, path in sources}
    for owner, key in _credential_fields(config):
        value = owner[key]
        if not isinstance(value, str) or not value:
            raise OperationsError("Credential file path is invalid")
        path = _checked_path(Path(value) if Path(value).is_absolute() else state / value)
        if path.parent != state / "secrets" or path not in selected or not path.name.endswith(".token"):
            raise OperationsError("Credentials must be recognized files inside state/secrets for a self-contained snapshot")


def _copy_regular(source: Path, destination: Path) -> None:
    _reject_links(source)
    _mkdir(destination.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(source, flags)
    with os.fdopen(fd, "rb") as original:
        if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
            raise OperationsError("Only regular files can be copied")
        output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                            getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(output_fd, "wb") as output:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                raise OperationsError("SQLite integrity check failed")
    except sqlite3.Error:
        raise OperationsError("SQLite integrity check failed") from None


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    _write_bytes(destination, b"")
    deadline = time.monotonic() + 30

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise OperationsError("SQLite snapshot timed out; pause writers and retry")

    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as original:
            with closing(sqlite3.connect(destination)) as output:
                original.backup(output, pages=256, progress=progress, sleep=0.05)
                output.execute("PRAGMA journal_mode=DELETE")
        _integrity(destination)
        # Windows FlushFileBuffers requires a writable handle. Reopen without
        # truncating the verified backup and keep flush failures fatal.
        with destination.open("r+b") as handle:
            os.fsync(handle.fileno())
    except sqlite3.Error:
        raise OperationsError("Cannot create a consistent SQLite snapshot") from None


def _publish(stage: Path, dest: Path, *, allow_empty=False) -> None:
    _reject_links(dest)
    if dest.exists():
        if not allow_empty or not dest.is_dir() or any(dest.iterdir()):
            raise OperationsError("Destination changed while preparing the operation")
        dest.rmdir()  # Fails safely if another process has added content.
    os.rename(stage, dest)


def create_snapshot(state_dir, destination) -> dict:
    """Export selected state through SQLite backup APIs and atomically publish it.

    Each database is consistent independently. Pause the hub before taking a
    snapshot that must represent one cross-database/configuration instant.
    Destination must not exist; its parent must exist on the target filesystem.
    """
    state = _checked_path(state_dir)
    if not state.is_dir():
        raise OperationsError("State directory does not exist")
    dest = _destination(state, destination)
    sources = _state_files(state)
    _validate_state_credentials(state, sources)
    stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.partial-", dir=dest.parent))
    try:
        files = []
        for relative, source in sources:
            target = stage / relative
            kind = _kind(relative)
            if kind == "sqlite":
                _sqlite_snapshot(source, target)
            else:
                if kind == "config":
                    _read_json(source)
                _copy_regular(source, target)
            files.append({"path": relative, "kind": kind,
                          "size": target.stat().st_size, "sha256": _hash(target)})
        manifest = {
            "format": _FORMAT, "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
            "contains_secrets": any(entry["kind"] == "secret" for entry in files),
            "source_corpus_included": False,
            "artifacts_included": False,
            "consistency": "per-database SQLite backup; pause writers for a common instant",
            "privacy": "Private, unencrypted export: includes credentials and indexed text",
        }
        _write_json(stage / "manifest.json", manifest)
        verify_snapshot(stage)
        _publish(stage, dest)
        return manifest
    except (OSError, sqlite3.Error):
        raise OperationsError("Snapshot could not be completed; destination was not published") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_snapshot(snapshot_dir) -> dict:
    """Check the strict manifest, file set, SHA-256 hashes, and SQLite integrity.

    Hashes detect damage; an unsigned manifest is not proof of authenticity.
    """
    root = _checked_path(snapshot_dir)
    if not root.is_dir():
        raise OperationsError("Snapshot directory does not exist")
    manifest = _read_json(root / "manifest.json", maximum=1024 * 1024)
    if manifest.get("format") != _FORMAT or manifest.get("version") != 1:
        raise OperationsError("Unsupported snapshot format")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1024:
        raise OperationsError("Invalid snapshot file manifest")
    listed = set()
    databases = []
    contains_secrets = False
    for entry in entries:
        if not isinstance(entry, dict):
            raise OperationsError("Invalid snapshot entry")
        relative = entry.get("path")
        kind = _kind(relative)
        size = entry.get("size")
        digest = entry.get("sha256")
        if (kind is None or relative in listed or entry.get("kind") != kind or
                type(size) is not int or size < 0 or not isinstance(digest, str) or
                not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise OperationsError("Invalid snapshot file metadata")
        path = root / relative
        _reject_links(path)
        try:
            if not path.is_file() or path.stat().st_size != size or _hash(path) != digest:
                raise OperationsError("Snapshot file is missing or has changed")
        except OSError:
            raise OperationsError("Snapshot file cannot be read") from None
        if kind == "sqlite":
            _integrity(path)
            databases.append(relative)
        if kind == "config":
            _read_json(path)
        contains_secrets |= kind == "secret"
        listed.add(relative)
    actual = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            _reject_links(Path(parent) / name)
        actual.update((Path(parent) / name).relative_to(root).as_posix() for name in files)
    if actual != listed | {"manifest.json"}:
        raise OperationsError("Snapshot contains unlisted or missing files")
    if manifest.get("contains_secrets") is not contains_secrets:
        raise OperationsError("Snapshot credential metadata is inconsistent")
    return {"ok": True, "file_count": len(listed), "contains_secrets": contains_secrets,
            "sqlite_databases": sorted(databases), "manifest": manifest}


def _reset_leases(path: Path) -> int:
    if not path.exists():
        return 0
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
            return 0
        required = {"status", "attempts", "max_attempts", "assigned_node", "lease_token",
                    "lease_expires_at", "next_attempt_at", "updated_at", "error",
                    "last_lease_token", "last_node", "last_action", "last_action_hash"}
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if not required <= columns:
            raise OperationsError("Queue schema is not supported for safe lease recovery")
        now = time.time()
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute("""UPDATE jobs SET
            status=CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
            assigned_node=NULL, lease_token=NULL, lease_expires_at=NULL,
            next_attempt_at=?, updated_at=?,
            error='Snapshot restored; previous lease invalidated',
            last_lease_token=NULL, last_node=NULL, last_action=NULL, last_action_hash=NULL
            WHERE status='running'""", (now, now)).rowcount
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='node_state'").fetchone():
            db.execute("DELETE FROM node_state")
        db.commit()
    _integrity(path)
    return changed


def _disarm_restored_config(stage: Path) -> tuple[dict, int, int]:
    config_path = stage / "config.json"
    projects = {}
    archived = 0
    if config_path.exists():
        config = _read_json(config_path)
        projects = config.get("projects", {})
        readers = config.get("readers", {})
        if not isinstance(projects, dict) or not isinstance(readers, dict):
            raise OperationsError("Restored project/reader configuration is invalid")
        for owner, key in _credential_fields(config):
            value = owner[key]
            if not isinstance(value, str):
                raise OperationsError("Restored credential path is invalid")
            # Preserve portability across Windows and POSIX absolute path syntax.
            relative = "secrets/" + PurePosixPath(value.replace("\\", "/")).name
            if _kind(relative) != "secret" or not (stage / relative).is_file():
                raise OperationsError("Restored configuration references a missing credential")
            owner[key] = relative
        for reader in readers.values():
            relative = reader.get("token_file") if isinstance(reader, dict) else None
            if relative and _kind(relative) == "secret" and (stage / relative).exists():
                target = stage / "recovery" / "reader-secrets" / Path(relative).name
                _mkdir(target.parent)
                os.rename(stage / relative, target)
                archived += 1
        config["state_dir"] = "."
        config["projects"] = {}
        config["readers"] = {}
        config_path.unlink()
        _write_json(config_path, config)
    rotated = 0
    if (stage / "secrets").exists():
        for token in sorted((stage / "secrets").glob("*.token")):
            token.unlink()
            _write_bytes(token, (secrets.token_urlsafe(32) + "\n").encode("ascii"))
            rotated += 1
    return projects, rotated, archived


def restore_snapshot(snapshot_dir, destination) -> dict:
    """Restore only into a new/empty directory; rotate credentials and disarm roots.

    The original hub must be stopped before a restored instance is promoted.
    Re-export workers/readers and explicitly reapprove project paths afterward.
    """
    source = _checked_path(snapshot_dir)
    dest = _destination(source, destination, allow_empty=True)
    verified = verify_snapshot(source)
    stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.partial-", dir=dest.parent))
    try:
        for entry in verified["manifest"]["files"]:
            _copy_regular(source / entry["path"], stage / entry["path"])
            # A source changing after verification still cannot be promoted.
            if _hash(stage / entry["path"]) != entry["sha256"]:
                raise OperationsError("Snapshot changed during restore")
        reset = _reset_leases(stage / "queue.sqlite3")
        projects, rotated, archived = _disarm_restored_config(stage)
        receipt = {"restored": True, "file_count": verified["file_count"],
                   "contains_secrets": verified["contains_secrets"], "leases_reset": reset,
                   "credentials_rotated": rotated, "reader_credentials_archived": archived,
                   "projects_requiring_reapproval": projects,
                   "source_corpus_restored": False,
                   "warnings": ["Stop the original hub before promoting this restored instance",
                                "Reapprove project roots and re-export worker/reader credentials",
                                "Original source files, artifacts, and model weights need separate backups"]}
        _write_json(stage / "recovery" / "receipt.json", receipt)
        _write_json(stage / "recovery" / "snapshot-manifest.json", verified["manifest"])
        _publish(stage, dest, allow_empty=True)
        return receipt
    except (OSError, sqlite3.Error):
        raise OperationsError("Restore could not be completed; destination was not populated") from None
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def doctor(config: dict) -> dict:
    """Best-effort local checks; no subprocess/network requests or secret dumps."""
    result = {"python": {"version": platform.python_version(),
                          "status": "ok" if sys.version_info >= (3, 10) else "unsupported"}}
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE VIRTUAL TABLE capability_check USING fts5(content)")
        result["sqlite"] = {"version": sqlite3.sqlite_version, "fts5": True, "status": "ok"}
    except sqlite3.Error:
        result["sqlite"] = {"version": sqlite3.sqlite_version, "fts5": False, "status": "unavailable"}
    try:
        target = Path(os.path.abspath(os.fspath(config.get("state_dir", "."))))
        exists = target.is_dir()
        while not target.exists() and target.parent != target:
            target = target.parent
        usage = shutil.disk_usage(target)
        result["disk"] = {"status": "ok", "state_directory_exists": exists,
                          "free_bytes": usage.free, "total_bytes": usage.total}
    except (OSError, ValueError, TypeError):
        result["disk"] = {"status": "unknown"}
    result["commands"] = {}
    for name in ("git", "ollama", "tailscale", "openclaw"):
        try:
            result["commands"][name] = {"available": shutil.which(name) is not None}
        except (OSError, ValueError):
            result["commands"][name] = {"available": False, "status": "unknown"}
    requested = config.get("port", config.get("listen_port", 8765))
    ports = {7000, 11434}
    if type(requested) is int and 1 <= requested <= 65535:
        ports.add(requested)
    result["ports"] = {}
    for port in sorted(ports):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", port))
            status = "available"
        except OSError as error:
            status = "in_use" if error.errno in {errno.EADDRINUSE, 10048} else "unknown"
        result["ports"][str(port)] = {"status": status}
    result["ok"] = result["python"]["status"] == "ok" and result["sqlite"]["fts5"]
    result["notes"] = ["An occupied port does not identify or verify its service",
                       "Optional command presence does not verify configuration or connectivity",
                       "Windows ACLs and physical startup/reboot behavior require host verification"]
    return result


def _mirror_source(source_url) -> tuple[str, Path | None]:
    if not isinstance(source_url, str) or not source_url or any(c in source_url for c in "\r\n\x00"):
        raise OperationsError("Invalid Git source")
    if Path(source_url).is_absolute():
        path = _checked_path(source_url)
        if not path.is_dir():
            raise OperationsError("Local Git source must be an existing directory")
        return str(path), path
    try:
        url = urlsplit(source_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password or
                url.query or url.fragment or not url.path or url.path == "/"):
            raise OperationsError("Use a credential-free HTTPS URL or an absolute local repository path")
        _ = url.port
    except ValueError:
        raise OperationsError("Use a credential-free HTTPS URL or an absolute local repository path") from None
    return source_url, None


def _git(args: list[str]) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise OperationsError("Git is not installed or is not on PATH")
    # Ignore ambient Git configuration overrides and interactive credential helpers.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull})
    argv = [executable, "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
            "-c", "protocol.file.allow=always", "-c", "protocol.ext.allow=never",
            "-c", "credential.helper=", "-c", f"core.hooksPath={os.devnull}",
            "-c", "http.followRedirects=false", *args]
    try:
        completed = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        raise OperationsError("Git operation could not finish; no subprocess output was exposed") from None
    if completed.returncode:
        raise OperationsError(f"Git operation failed (exit {completed.returncode}); inspect the source and access locally")
    return completed.stdout.strip()


def mirror_repository(source_url, destination, update=False) -> dict:
    """Create/update only a managed bare mirror. Never push or run shell commands."""
    source, source_path = _mirror_source(source_url)
    dest = _checked_path(destination)
    if source_path and _overlap(source_path, dest):
        raise OperationsError("Git source and mirror destination must not overlap")
    if not dest.parent.is_dir():
        raise OperationsError("Mirror parent directory must already exist")
    if update:
        if not dest.is_dir():
            raise OperationsError("Managed mirror does not exist")
        marker = _read_json(dest / _MIRROR_MARKER)
        if marker != {"version": 1, "source": source}:
            raise OperationsError("Destination is not a managed mirror for this source")
        for parent, dirs, files in os.walk(dest, followlinks=False):
            for name in dirs + files:
                _reject_links(Path(parent) / name)
        if (_git(["-C", str(dest), "rev-parse", "--is-bare-repository"]) != "true" or
                _git(["-C", str(dest), "config", "--get", "remote.origin.url"]) != source or
                _git(["-C", str(dest), "config", "--get", "remote.origin.mirror"]) != "true"):
            raise OperationsError("Destination is not the expected bare mirror")
        _git(["-C", str(dest), "config", "remote.origin.pushurl", "disabled://push-blocked"])
        _git(["-C", str(dest), "fetch", "--prune", "origin", "+refs/*:refs/*"])
    else:
        if dest.exists():
            raise OperationsError("Mirror destination already exists")
        stage = Path(tempfile.mkdtemp(prefix=f".{dest.name}.partial-", dir=dest.parent))
        try:
            _git(["clone", "--mirror", "--no-hardlinks", "--", source, str(stage)])
            _git(["-C", str(stage), "config", "remote.origin.pushurl", "disabled://push-blocked"])
            _write_json(stage / _MIRROR_MARKER, {"version": 1, "source": source})
            _publish(stage, dest)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return {"ok": True, "updated": bool(update), "pull_only": True,
            "notes": ["Mirror updates can propagate rewritten or deleted refs; keep versioned backups",
                      "Git LFS objects, submodules, wiki, and hosting metadata require separate handling"]}
