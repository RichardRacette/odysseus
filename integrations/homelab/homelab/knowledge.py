"""Opt-in project evidence, exact citations and rebuildable SQLite indexes.

This store never discovers or automatically opens Odysseus personal data. Project roots are supplied by
the operator; initialization performs no source scan or network request.
"""
from __future__ import annotations

import bisect
import hashlib
import heapq
import json
import os
import re
import sqlite3
import stat
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .embeddings import normalized_vector

MAX_FILE_BYTES = 1024 * 1024
MAX_PROJECT_FILES = 1000
MAX_PROJECT_BYTES = 32 * 1024 * 1024
MAX_PROJECT_CHUNKS = 10000
MAX_NOTE_BYTES = 8 * 1024 * 1024
MAX_SCAN_ENTRIES = 20000
CHUNK_CHARS = 1600
ALLOWED_EXTENSIONS = frozenset({".md", ".txt", ".rst", ".json", ".py", ".js", ".ts",
                                ".tsx", ".jsx", ".yaml", ".yml", ".toml", ".csv", ".sql",
                                ".html", ".css", ".sh", ".ps1", ".ini", ".cfg"})
EXCLUDED_DIRS = frozenset({"node_modules", "__pycache__", "venv", "env", "dist", "build",
                          "vendor", "coverage", "backups", "secrets", "credentials"})
SENSITIVE_NAME = re.compile(r"(?:^|[._-])(?:secrets?|tokens?|passwords?|credentials?|"
                            r"private[._-]?keys?|api[._-]?keys?)(?:[._-]|$)", re.I)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _root_path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("Project roots must be absolute directory paths")
    root = Path(value)
    if not root.is_absolute() or ".." in root.parts or root == Path(root.anchor):
        raise ValueError("Approve an explicit absolute project directory, not a filesystem root")
    if root.name.startswith("."):
        raise ValueError("Hidden project roots are not eligible for ingestion")
    # Use canonical paths in config (on macOS, /private/tmp rather than /tmp).
    # Reject a symlink/junction root or ancestor rather than broadening authority.
    if os.path.normcase(str(root.resolve(strict=False))) != os.path.normcase(str(root)):
        raise ValueError("Project roots and their ancestors must not be symlinks; use the canonical path")
    for parent in (root, *root.parents):
        if parent.is_symlink():
            raise ValueError("Project roots and their ancestors must not be symlinks")
    return root


def _excluded_name(name, *, directory=False):
    lower = name.lower()
    if name.startswith("."):
        return "hidden path"
    if SENSITIVE_NAME.search(name) or lower.startswith(("id_rsa", "id_ed25519", "id_ecdsa")):
        return "credential or secret filename"
    if directory:
        return "generated or sensitive directory" if lower in EXCLUDED_DIRS else None
    if Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".keystore", ".kdbx"}:
        return "private key or credential file"
    if Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return "unsupported file type"
    return None


def _chunks(text):
    line_starts = [0] + [match.end() for match in re.finditer(r"\r\n|\r|\n", text)]
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            newline = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if newline != -1:
                end = newline + 1
        part = text[start:end]
        if part.strip():
            yield (part, bisect.bisect_right(line_starts, start),
                   bisect.bisect_right(line_starts, end - 1), start, end)
        start = end


def validate_projects(projects) -> dict[str, Path]:
    """Validate all approved roots without creating a database or reading files.

    Root path metadata is inspected to reject symlinks and overlapping scopes.
    Missing roots are permitted so a temporarily offline drive can still have
    its existing records reported as stale instead of blocking recovery.
    """
    if not isinstance(projects, dict):
        raise ValueError("Projects must be an explicit name-to-root mapping")
    validated = {}
    for name, value in projects.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", name):
            raise ValueError("Project names must be nonempty simple identifiers")
        root = _root_path(value)
        for previous in validated.values():
            if root == previous or root in previous.parents or previous in root.parents:
                raise ValueError("Approved project roots must not overlap")
        validated[name] = root
    return validated


class KnowledgeStore:
    """Single-host store, safe for threads in the same service process.

    ``projects`` is an exact project-name -> canonical absolute-root mapping.
    Different projects may not overlap: this prevents an accidental broad root
    from making a supposedly separate project's documents searchable twice.
    """

    def __init__(self, db_path, projects: dict[str, str], embedder=None):
        self.projects = validate_projects(projects)
        self.db_path = Path(db_path).absolute()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL, path TEXT NOT NULL,
                    root TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'current',
                    error TEXT, UNIQUE(project,path)
                );
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, project TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('source','note','inference','decision')),
                    text TEXT NOT NULL, source_id TEXT REFERENCES sources(id) ON DELETE CASCADE,
                    start_line INTEGER, end_line INTEGER, start_char INTEGER, end_char INTEGER,
                    source_refs TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_project ON records(project);
                CREATE INDEX IF NOT EXISTS records_source ON records(source_id);
                CREATE TABLE IF NOT EXISTS embeddings (
                    record_id TEXT PRIMARY KEY REFERENCES records(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, dimension INTEGER NOT NULL, vector BLOB NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                    text, content='records', content_rowid='rowid', tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
                    INSERT INTO records_fts(rowid,text) VALUES(new.rowid,new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
                    INSERT INTO records_fts(records_fts,rowid,text) VALUES('delete',old.rowid,old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
                    INSERT INTO records_fts(records_fts,rowid,text) VALUES('delete',old.rowid,old.text);
                    INSERT INTO records_fts(rowid,text) VALUES(new.rowid,new.text);
                END;
            """)
            if sqlite3.sqlite_version_info >= (3, 42, 0):
                self._db.execute("INSERT INTO records_fts(records_fts,rank) VALUES('secure-delete',1)")
            self._db.commit()
        except BaseException:
            self._db.close()
            raise

    def close(self):
        with self._lock:
            self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _project(self, project):
        if not isinstance(project, str) or project not in self.projects:
            raise ValueError("An exact configured project is required")
        return self.projects[project]

    def scan(self, project):
        """Return a bounded dry-run plan. Never read file contents or write rows."""
        root = self._project(project)
        result = {"project": project, "selected": [], "selected_count": 0, "selected_bytes": 0,
                  "excluded": [], "excluded_count": 0, "errors": [], "complete": True}

        def exclude(path, reason):
            result["excluded_count"] += 1
            if len(result["excluded"]) < 1000:
                result["excluded"].append({"path": path, "reason": reason})

        def failure(path, reason):
            result["errors"].append({"path": path, "reason": reason})
            result["complete"] = False

        try:
            _root_path(root)
            if not root.is_dir():
                raise ValueError()
        except (OSError, ValueError):
            failure(".", "approved root is unavailable or unsafe")
            return result
        scanned = 0
        def walk_error(error):
            failure(".", "directory traversal failed; no deletion inference is permitted")
        for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
            dirs.sort()
            files.sort()
            scanned += len(dirs) + len(files)
            if scanned > MAX_SCAN_ENTRIES:
                failure(".", "directory scan entry limit exceeded")
                break
            for name in list(dirs):
                path = Path(folder) / name
                reason = "symlink directory" if path.is_symlink() else _excluded_name(name, directory=True)
                if not reason:
                    try:
                        if path.resolve(strict=True) != path:
                            reason = "junction or noncanonical directory"
                    except OSError:
                        reason = "unreadable directory"
                        failure(str(path.relative_to(root)), reason)
                if reason:
                    dirs.remove(name)
                    exclude(path.relative_to(root).as_posix(), reason)
            for name in files:
                path = Path(folder) / name
                relative = path.relative_to(root).as_posix()
                reason = "symlink file" if path.is_symlink() else _excluded_name(name)
                if reason:
                    exclude(relative, reason)
                    continue
                try:
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        exclude(relative, "not a regular file")
                        continue
                    if info.st_size > MAX_FILE_BYTES:
                        exclude(relative, "file size limit exceeded")
                        continue
                except OSError:
                    failure(relative, "file metadata is unreadable")
                    continue
                result["selected"].append(relative)
                result["selected_bytes"] += info.st_size
                if len(result["selected"]) > MAX_PROJECT_FILES or result["selected_bytes"] > MAX_PROJECT_BYTES:
                    failure(".", "project file or byte limit exceeded")
                    break
            if not result["complete"]:
                break
        result["selected"].sort()
        result["selected_count"] = len(result["selected"])
        return result

    def _read_source(self, project, relative):
        root = self._project(project)
        _root_path(root)
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("source path is outside approved root")
        for part in relative_path.parts[:-1]:
            if _excluded_name(part, directory=True):
                raise ValueError("source directory is excluded")
        if _excluded_name(relative_path.name):
            raise ValueError("source filename is excluded")
        path = root / relative_path
        if path.resolve(strict=True) != path:
            raise ValueError("source has a symlink or junction")
        # POSIX openat + O_NOFOLLOW binds every component to an opened directory;
        # changing a path between validation and read cannot follow a new link.
        descriptors = []
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                parent = os.open(path.anchor, flags)
                descriptors.append(parent)
                for part in path.parts[1:-1]:
                    parent = os.open(part, flags, dir_fd=parent)
                    descriptors.append(parent)
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            else:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise ValueError("source is not a bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(descriptor)
            current = path.lstat()
            identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
            version = lambda item: (*identity(item), item.st_ctime_ns)
            # Windows can report change time from fstat and creation time from
            # lstat. Compare ctime only between descriptor observations, while
            # still checking that the current path identifies the file read.
            if len(data) > MAX_FILE_BYTES or version(before) != version(after) or identity(after) != identity(current):
                raise ValueError("source changed during read")
            if path.resolve(strict=True) != path:
                raise ValueError("source path changed during read")
            # Keep ctime-based detection of same-size, restored-mtime writes
            # through the final path checks without mixing timestamp semantics.
            if version(after) != version(os.fstat(descriptor)):
                raise ValueError("source changed during read")
            if b"\x00" in data:
                raise ValueError("binary content is not supported")
            return data.decode("utf-8"), _digest(data), len(data)
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def ingest(self, project):
        with self._lock:
            root = self._project(project)
            plan = self.scan(project)
            result = {"project": project, "added": 0, "updated": 0, "unchanged": 0, "removed": 0,
                      "indexed_chunks": 0, "embedded_chunks": 0, "errors": list(plan["errors"]),
                      "warnings": [], "excluded_count": plan["excluded_count"]}
            if not plan["complete"]:
                with self._db:
                    self._db.execute("UPDATE sources SET state='stale',error='scan incomplete' WHERE project=?", (project,))
                return result
            selected = set(plan["selected"])
            old_sources = {row["path"]: row for row in self._db.execute("SELECT * FROM sources WHERE project=?", (project,))}
            # Only a complete traversal can establish that a previously indexed
            # path disappeared or is now excluded by the ingestion policy.
            with self._db:
                for path, old in old_sources.items():
                    if path not in selected:
                        self._db.execute("DELETE FROM sources WHERE id=?", (old["id"],))
                        result["removed"] += 1
            for relative in plan["selected"]:
                old = old_sources.get(relative)
                try:
                    text, digest, size = self._read_source(project, relative)
                    parts = list(_chunks(text))
                    total = self._db.execute("SELECT count(*) FROM records WHERE project=?", (project,)).fetchone()[0]
                    previous = self._db.execute("SELECT count(*) FROM records WHERE source_id=?", (old["id"],)).fetchone()[0] if old else 0
                    if total - previous + len(parts) > MAX_PROJECT_CHUNKS:
                        raise ValueError("project chunk limit exceeded")
                except (OSError, ValueError) as exc:
                    reason = "source is unreadable, changed, excluded, or exceeds a limit"
                    result["errors"].append({"path": relative, "reason": reason})
                    if old:
                        with self._db:
                            self._db.execute("UPDATE sources SET state='stale',error=? WHERE id=?", (reason, old["id"]))
                    continue
                if old and old["sha256"] == digest and old["root"] == str(root):
                    with self._db:
                        self._db.execute("UPDATE sources SET state='current',error=NULL WHERE id=?", (old["id"],))
                    result["unchanged"] += 1
                    continue
                source_id = _digest(f"{project}\0{relative}".encode())
                stamp = _now()
                with self._db:
                    if old:
                        self._db.execute("DELETE FROM sources WHERE id=?", (old["id"],))
                    self._db.execute("INSERT INTO sources(id,project,path,root,sha256,byte_size,indexed_at) VALUES(?,?,?,?,?,?,?)",
                                     (source_id, project, relative, str(root), digest, size, stamp))
                    for chunk, first, last, start, end in parts:
                        record_id = _digest(f"{project}\0{root}\0{relative}\0{digest}\0{start}\0{end}".encode())
                        self._db.execute("""INSERT INTO records(id,project,kind,text,source_id,start_line,end_line,
                                         start_char,end_char,created_at) VALUES(?,?,'source',?,?,?,?,?,?,?)""",
                                         (record_id, project, chunk, source_id, first, last, start, end, stamp))
                result["updated" if old else "added"] += 1
                result["indexed_chunks"] += len(parts)
            if self.embedder is not None:
                result["embedded_chunks"] = self._index_embeddings(project, result["warnings"])
            return result

    def _index_embeddings(self, project, warnings):
        try:
            fingerprint = self.embedder.fingerprint()
            if not isinstance(fingerprint, str) or not fingerprint:
                raise ValueError("invalid model fingerprint")
            warnings.extend(getattr(self.embedder, "warnings", []))
            rows = self._db.execute("""SELECT r.id,r.text FROM records r
                LEFT JOIN sources s ON s.id=r.source_id LEFT JOIN embeddings e ON e.record_id=r.id
                WHERE r.project=? AND (s.id IS NULL OR s.state='current')
                AND (e.record_id IS NULL OR e.fingerprint!=?) ORDER BY r.id""", (project, fingerprint)).fetchall()
            count = 0
            for offset in range(0, len(rows), 16):
                batch = rows[offset:offset + 16]
                vectors = self.embedder.embed([row["text"] for row in batch], purpose="document")
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise ValueError("invalid embedding count")
                normalized = [normalized_vector(vector) for vector in vectors]
                if len({len(vector) for vector in normalized}) != 1:
                    raise ValueError("inconsistent embedding dimensions")
                with self._db:
                    for row, vector in zip(batch, normalized):
                        self._db.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?,?)",
                                         (row["id"], fingerprint, len(vector), struct.pack(f"<{len(vector)}f", *vector)))
                count += len(batch)
            return count
        except Exception:
            warnings.append("Embeddings are unavailable or incomplete; lexical retrieval remains available. Retry ingest after recovery.")
            return locals().get("count", 0)

    def _row(self, record_id, project):
        return self._db.execute("""SELECT r.*,s.path AS source_path,s.root AS source_root,
            s.sha256 AS source_sha256,s.state AS source_state,s.indexed_at
            FROM records r LEFT JOIN sources s ON s.id=r.source_id
            WHERE r.id=? AND r.project=?""", (record_id, project)).fetchone()

    def _source_current(self, row, cache, warnings):
        if row["source_id"] is None:
            return True
        source_id = row["source_id"]
        if source_id not in cache:
            try:
                if row["source_state"] != "current" or row["source_root"] != str(self.projects[row["project"]]):
                    raise ValueError()
                _, digest, _ = self._read_source(row["project"], row["source_path"])
                cache[source_id] = digest == row["source_sha256"]
            except (OSError, ValueError):
                cache[source_id] = False
            if not cache[source_id]:
                warnings.append("An indexed source is stale or unavailable and was withheld; run ingest to refresh it.")
        return cache[source_id]

    def _result(self, row, cache=None, warnings=None):
        cache = {} if cache is None else cache
        warnings = [] if warnings is None else warnings
        refs = json.loads(row["source_refs"])
        evidence_status = "source" if row["kind"] == "source" else "unreferenced"
        if refs:
            evidence_status = "referenced"
            for ref in refs:
                current = self._row(ref["record_id"], row["project"])
                if current is None or not self._source_current(current, cache, warnings):
                    ref["status"] = "stale_or_missing"
                    evidence_status = "stale_references"
                else:
                    ref["status"] = "current"
        result = {key: row[key] for key in ("id", "project", "kind", "text", "created_at",
                  "source_path", "source_sha256", "start_line", "end_line", "start_char", "end_char")}
        result.update({"chunk_sha256": _digest(row["text"].encode()), "source_refs": refs,
                       "evidence_status": evidence_status, "untrusted_content": True})
        if row["kind"] == "source":
            result["root_id"] = _digest(row["source_root"].encode())[:16]
            result["citation"] = f"{row['project']}:{row['source_path']}:L{row['start_line']}-L{row['end_line']}@sha256:{row['source_sha256']}"
        else:
            result["citation"] = f"{row['project']}:record:{row['id']}"
        return result

    def search(self, project, query, limit=5, mode="lexical"):
        with self._lock:
            self._project(project)
            if not isinstance(query, str) or len(query) > 2000:
                raise ValueError("Search query must be text of at most 2000 characters")
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
                raise ValueError("Search limit must be between 1 and 50")
            if mode not in ("lexical", "hybrid"):
                raise ValueError("Search mode must be lexical or hybrid")
            warnings = []
            if self._db.execute("SELECT count(*) FROM sources WHERE project=? AND state='stale'", (project,)).fetchone()[0]:
                warnings.append("This project has stale sources; their content is excluded until a successful ingest.")
            tokens = re.findall(r"\w+", query, flags=re.UNICODE)[:64]
            lexical = []
            if tokens:
                expression = " OR ".join('"' + token + '"' for token in tokens)
                lexical = [row[0] for row in self._db.execute("""SELECT r.id FROM records_fts
                    JOIN records r ON r.rowid=records_fts.rowid LEFT JOIN sources s ON s.id=r.source_id
                    WHERE records_fts MATCH ? AND r.project=? AND (s.id IS NULL OR s.state='current')
                    ORDER BY bm25(records_fts),r.id LIMIT ?""", (expression, project, MAX_PROJECT_CHUNKS))]
            order = lexical
            actual_mode = "lexical"
            scores = {record_id: 1.0 / (rank + 60) for rank, record_id in enumerate(lexical, 1)}
            if mode == "hybrid" and query.strip():
                if self.embedder is None:
                    warnings.append("Embeddings are disabled; returned lexical results.")
                else:
                    try:
                        fingerprint = self.embedder.fingerprint()
                        warnings.extend(getattr(self.embedder, "warnings", []))
                        vectors = self.embedder.embed([query], purpose="query")
                        if not isinstance(vectors, list) or len(vectors) != 1:
                            raise ValueError()
                        vector = normalized_vector(vectors[0])
                        candidates = []
                        for item in self._db.execute("""SELECT r.id,e.vector,e.dimension FROM embeddings e
                            JOIN records r ON r.id=e.record_id LEFT JOIN sources s ON s.id=r.source_id
                            WHERE r.project=? AND e.fingerprint=? AND (s.id IS NULL OR s.state='current')""",
                            (project, fingerprint)):
                            if item["dimension"] != len(vector) or len(item["vector"]) != 4 * len(vector):
                                warnings.append("Incompatible stored vectors were excluded; re-ingest this project.")
                                continue
                            stored = normalized_vector(list(struct.unpack(f"<{len(vector)}f", item["vector"])), len(vector))
                            similarity = sum(left * right for left, right in zip(stored, vector))
                            candidates.append((similarity, item["id"]))
                        if not candidates:
                            warnings.append("No compatible indexed embeddings are available; returned lexical results. Run ingest.")
                        else:
                            semantic = [record_id for _, record_id in heapq.nlargest(100, candidates)]
                            for rank, record_id in enumerate(semantic, 1):
                                scores[record_id] = scores.get(record_id, 0) + 1.0 / (rank + 60)
                            order = sorted(scores, key=lambda key: (-scores[key], key))
                            actual_mode = "hybrid"
                            current_count = self._db.execute("""SELECT count(*) FROM records r LEFT JOIN sources s ON s.id=r.source_id
                                WHERE r.project=? AND (s.id IS NULL OR s.state='current')""", (project,)).fetchone()[0]
                            if len(candidates) < current_count:
                                warnings.append("Only part of this project has compatible embeddings; lexical results also contribute.")
                    except Exception:
                        warnings.append("Semantic search is unavailable; returned lexical results.")
            cache = {}
            results = []
            for record_id in order:
                row = self._row(record_id, project)
                if row is None or not self._source_current(row, cache, warnings):
                    continue
                result = self._result(row, cache, warnings)
                result["score"] = scores[record_id]
                result["score_type"] = "reciprocal_rank_fusion" if actual_mode == "hybrid" else "lexical_rank"
                results.append(result)
                if len(results) >= limit:
                    break
            return {"results": results, "mode": actual_mode, "warnings": list(dict.fromkeys(warnings))}

    def remember(self, project, text, kind="note", source_refs=None):
        with self._lock:
            self._project(project)
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError("Memory text must contain one to 16000 characters")
            if kind not in ("note", "inference", "decision"):
                raise ValueError("Remembered records must be note, inference, or decision; sources come from ingestion")
            if source_refs is None:
                source_refs = []
            if not isinstance(source_refs, list) or len(source_refs) > 32:
                raise ValueError("Source references must be a list of at most 32 same-project record IDs")
            refs = []
            for ref in source_refs:
                record_id = ref.get("record_id") if isinstance(ref, dict) else ref
                if not isinstance(record_id, str):
                    raise ValueError("Each reference must contain a record_id")
                row = self._row(record_id, project)
                if row is None:
                    raise ValueError("Reference not found in this project")
                refs.append({"record_id": row["id"], "kind": row["kind"], "source_path": row["source_path"],
                             "source_sha256": row["source_sha256"], "start_line": row["start_line"],
                             "end_line": row["end_line"], "text_sha256": _digest(row["text"].encode())})
            if self._db.execute("SELECT count(*) FROM records WHERE project=?", (project,)).fetchone()[0] >= MAX_PROJECT_CHUNKS:
                raise ValueError("Project record limit exceeded")
            note_bytes = self._db.execute("SELECT coalesce(sum(length(CAST(text AS BLOB))),0) FROM records WHERE project=? AND source_id IS NULL", (project,)).fetchone()[0]
            if note_bytes + len(text.strip().encode("utf-8")) > MAX_NOTE_BYTES:
                raise ValueError("Project authored-note byte limit exceeded")
            record_id = uuid.uuid4().hex
            with self._db:
                self._db.execute("INSERT INTO records(id,project,kind,text,source_refs,created_at) VALUES(?,?,?,?,?,?)",
                                 (record_id, project, kind, text.strip(), json.dumps(refs), _now()))
            warnings = []
            if self.embedder is not None:
                self._index_embeddings(project, warnings)
            result = self._result(self._row(record_id, project), warnings=warnings)
            result["warnings"] = list(dict.fromkeys(warnings))
            return result

    def forget(self, project, record_id):
        with self._lock:
            self._project(project)
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("An exact record ID is required")
            with self._db:
                cursor = self._db.execute("DELETE FROM records WHERE project=? AND id=?", (project, record_id))
                deleted = cursor.rowcount == 1
                if deleted:
                    # Discard old FTS postings as well as its visible row. This
                    # is not a promise to erase prior backups or SSD snapshots.
                    self._db.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
            return deleted

    def status(self, project=None):
        with self._lock:
            if project is not None:
                self._project(project)
            names = [project] if project is not None else list(self.projects)
            totals = {"projects": names, "sources": 0, "stale_sources": 0, "records": 0,
                      "embedded_records": 0, "embeddings_enabled": self.embedder is not None}
            for name in names:
                totals["sources"] += self._db.execute("SELECT count(*) FROM sources WHERE project=?", (name,)).fetchone()[0]
                totals["stale_sources"] += self._db.execute("SELECT count(*) FROM sources WHERE project=? AND state='stale'", (name,)).fetchone()[0]
                totals["records"] += self._db.execute("SELECT count(*) FROM records WHERE project=?", (name,)).fetchone()[0]
                totals["embedded_records"] += self._db.execute("SELECT count(*) FROM embeddings e JOIN records r ON r.id=e.record_id WHERE r.project=?", (name,)).fetchone()[0]
            return totals

    def rebuild_index(self, project):
        """Rebuild derived FTS and this project's optional vectors from records.

        SQLite's FTS rebuild applies to the shared derived index, but changes no
        records or source permissions. The vector rebuild is project-scoped.
        This operation does not reread sources; use ingest for changed files.
        """
        with self._lock:
            self._project(project)
            with self._db:
                self._db.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
                self._db.execute("DELETE FROM embeddings WHERE record_id IN (SELECT id FROM records WHERE project=?)", (project,))
            warnings = []
            embedded = self._index_embeddings(project, warnings) if self.embedder is not None else 0
            return {"project": project, "rebuilt": True, "embedded_chunks": embedded, "warnings": warnings}
