"""Behavioral tests for the opt-in, project-scoped evidence store."""
import hashlib
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from homelab.knowledge import KnowledgeStore


def changed_stat(info, **changes):
    """Keep real OS metadata except the fields needed for a portability/race case."""
    values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
    values.update(changes)
    return SimpleNamespace(**values)


class LocalEmbeddings:
    """Deterministic injected encoder; tests never download or contact a model."""
    warnings = []
    identity = "test-model-v1"
    fail = False

    def fingerprint(self):
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.identity

    def embed(self, texts, *, purpose="document"):
        if self.fail:
            raise RuntimeError("model unavailable")
        return [[1.0, 0.0] if "backup" in text.lower() else [0.0, 1.0]
                for text in texts]


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.a = self.base / "project-a"
        self.b = self.base / "project-b"
        self.a.mkdir()
        self.b.mkdir()
        self.db = self.base / "knowledge.sqlite3"
        self.store = KnowledgeStore(self.db, {"a": str(self.a), "b": str(self.b)})
        self.addCleanup(self.store.close)

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def hits(self, query, project="a", **kwargs):
        return self.store.search(project, query, **kwargs)["results"]

    def test_empty_configuration_does_not_scan_or_ingest(self):
        self.write(self.a / "note.md", "private orchard")
        with KnowledgeStore(self.base / "empty.db", {}) as empty:
            self.assertEqual(empty.status()["records"], 0)
            with self.assertRaises(ValueError):
                empty.scan("a")

    def test_scan_is_dry_run_and_excludes_sensitive_files(self):
        self.write(self.a / "readme.md", "orchard")
        for name in (".env", "credentials.json", "api-key.txt", "private_key.pem",
                     ".aws/config.txt", "node_modules/pkg/readme.md", "id_rsa"):
            self.write(self.a / name, "sensitive")
        plan = self.store.scan("a")
        self.assertEqual(plan["selected"], ["readme.md"])
        self.assertGreaterEqual(plan["excluded_count"], 7)
        self.assertEqual(self.store.status()["records"], 0)

    def test_exact_provenance_and_duplicate_sources_survive(self):
        text = "# Lab\nFirst line.\nBackup policy is weekly.\n"
        self.write(self.a / "one.md", text)
        self.write(self.a / "two.md", text)
        result = self.store.ingest("a")
        self.assertEqual(result["added"], 2)
        hits = self.hits("backup")
        self.assertEqual({row["source_path"] for row in hits}, {"one.md", "two.md"})
        self.assertEqual(len({row["id"] for row in hits}), 2)
        for row in hits:
            self.assertEqual(row["source_sha256"], hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(row["text"], text)
            self.assertEqual((row["start_line"], row["end_line"]), (1, 3))
            self.assertEqual(row["kind"], "source")

    def test_projects_are_isolated_and_unknown_scope_fails(self):
        self.write(self.a / "a.md", "orchard apples")
        self.write(self.b / "b.md", "orchard pears")
        self.store.ingest("a")
        self.store.ingest("b")
        self.assertEqual(len(self.hits("orchard")), 1)
        self.assertIn("apples", self.hits("orchard")[0]["text"])
        with self.assertRaises(ValueError):
            self.store.search("*", "orchard")
        with self.assertRaises(ValueError):
            self.store.search(None, "orchard")

    def test_changed_and_deleted_sources_remove_old_hits(self):
        source = self.a / "state.md"
        self.write(source, "old orchard")
        self.store.ingest("a")
        self.assertEqual(self.store.ingest("a")["unchanged"], 1)
        self.write(source, "new vineyard")
        self.assertEqual(self.store.ingest("a")["updated"], 1)
        self.assertEqual(self.hits("orchard"), [])
        self.assertEqual(len(self.hits("vineyard")), 1)
        source.unlink()
        self.assertEqual(self.store.ingest("a")["removed"], 1)
        self.assertEqual(self.hits("vineyard"), [])

    def test_unreadable_source_is_stale_and_not_returned(self):
        source = self.a / "state.md"
        self.write(source, "old orchard")
        self.store.ingest("a")
        source.write_bytes(b"\xff\xfe")
        result = self.store.ingest("a")
        self.assertTrue(result["errors"])
        self.assertEqual(self.store.status("a")["stale_sources"], 1)
        answer = self.store.search("a", "orchard")
        self.assertEqual(answer["results"], [])
        self.assertTrue(answer["warnings"])

    def test_search_checks_changed_source_before_citing_it(self):
        source = self.a / "state.md"
        self.write(source, "old orchard")
        self.store.ingest("a")
        self.write(source, "new vineyard")
        answer = self.store.search("a", "orchard")
        self.assertEqual(answer["results"], [])
        self.assertTrue(answer["warnings"])

    def test_stable_source_survives_different_fd_and_path_ctime_semantics(self):
        source = self.a / "policy.md"
        self.write(source, "backup each week\n")
        fstat = os.fstat

        def descriptor_stat(fd):
            info = fstat(fd)
            # Windows fstat can report change time while lstat reports birth time.
            return changed_stat(info, st_ctime_ns=info.st_ctime_ns + 1_000_000)

        with patch("homelab.knowledge.os.fstat", side_effect=descriptor_stat):
            result = self.store.ingest("a")
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["added"], 1)
            hits = self.hits("backup")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["text"], "backup each week\n")
        self.assertEqual(hits[0]["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_descriptor_ctime_change_with_unchanged_size_and_mtime_is_rejected(self):
        self.write(self.a / "policy.md", "backup each week")
        fstat = os.fstat
        reads = 0

        def changed_descriptor_stat(fd):
            nonlocal reads
            reads += 1
            info = fstat(fd)
            return changed_stat(info, st_ctime_ns=info.st_ctime_ns + (reads > 1))

        with patch("homelab.knowledge.os.fstat", side_effect=changed_descriptor_stat):
            result = self.store.ingest("a")
        self.assertEqual(result["added"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(self.store.status("a")["records"], 0)

    def test_path_and_descriptor_identity_mismatch_is_rejected(self):
        self.write(self.a / "policy.md", "backup each week")
        fstat = os.fstat

        def different_file_stat(fd):
            info = fstat(fd)
            return changed_stat(info, st_ino=info.st_ino + 1)

        with patch("homelab.knowledge.os.fstat", side_effect=different_file_stat):
            result = self.store.ingest("a")
        self.assertEqual(result["added"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(self.store.status("a")["records"], 0)

    def test_descriptor_change_during_final_path_check_is_rejected(self):
        source = self.a / "policy.md"
        self.write(source, "backup each week")
        fstat = os.fstat
        resolve = Path.resolve
        descriptor_reads = 0
        path_checked = False

        def descriptor_stat(fd):
            nonlocal descriptor_reads
            descriptor_reads += 1
            info = fstat(fd)
            return changed_stat(info, st_ctime_ns=info.st_ctime_ns + path_checked)

        def resolve_after_read(path, *args, **kwargs):
            nonlocal path_checked
            result = resolve(path, *args, **kwargs)
            if path == source and descriptor_reads >= 2:
                path_checked = True
            return result

        with patch("homelab.knowledge.os.fstat", side_effect=descriptor_stat), \
                patch.object(Path, "resolve", resolve_after_read):
            result = self.store.ingest("a")
        self.assertEqual(result["added"], 0)
        self.assertTrue(result["errors"])
        self.assertEqual(self.store.status("a")["records"], 0)

    def test_missing_root_marks_stale_without_deletion(self):
        self.write(self.a / "state.md", "orchard")
        self.store.ingest("a")
        self.a.rename(self.base / "offline")
        result = self.store.ingest("a")
        self.assertTrue(result["errors"])
        self.assertEqual(result["removed"], 0)
        self.assertEqual(self.store.status("a")["sources"], 1)
        self.assertEqual(self.hits("orchard"), [])

    def test_symlink_files_and_directories_are_never_indexed(self):
        self.write(self.b / "outside.md", "outside orchard")
        try:
            (self.a / "escape.md").symlink_to(self.b / "outside.md")
            (self.a / "folder").symlink_to(self.b, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this host")
        self.assertEqual(self.store.scan("a")["selected"], [])
        self.store.ingest("a")
        self.assertEqual(self.hits("orchard"), [])

    def test_symlink_root_and_ancestor_are_rejected(self):
        try:
            link = self.base / "link"
            link.symlink_to(self.a, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation unavailable on this host")
        self.a.joinpath("child").mkdir()
        for root in (link, link / "child"):
            with self.assertRaises(ValueError):
                KnowledgeStore(self.base / "other.db", {"unsafe": str(root)})

    def test_relative_or_filesystem_root_is_rejected(self):
        for root in ("relative/project", str(Path(self.a.anchor))):
            with self.assertRaises(ValueError):
                KnowledgeStore(self.base / "other.db", {"unsafe": root})

    def test_large_file_and_corpus_cap_are_visible(self):
        self.write(self.a / "too-big.md", "x" * (1024 * 1024 + 1))
        plan = self.store.scan("a")
        self.assertEqual(plan["selected"], [])
        self.assertTrue(any("size" in item["reason"] for item in plan["excluded"]))
        for name in ("a.md", "b.md", "c.md"):
            self.write(self.a / name, "orchard")
        with patch("homelab.knowledge.MAX_PROJECT_FILES", 2):
            result = self.store.ingest("a")
        self.assertTrue(result["errors"])
        self.assertEqual(self.store.status("a")["records"], 0)

    def test_notes_preserve_kind_and_resolved_source_references(self):
        self.write(self.a / "policy.md", "backup each week")
        self.store.ingest("a")
        evidence = self.hits("backup")[0]
        decision = self.store.remember("a", "Retain the weekly backup policy.",
                                       kind="decision", source_refs=[evidence["id"]])
        self.assertEqual(decision["kind"], "decision")
        self.assertEqual(decision["source_refs"][0]["source_sha256"], evidence["source_sha256"])
        self.assertEqual(self.hits("retain")[0]["kind"], "decision")
        with self.assertRaises(ValueError):
            self.store.remember("b", "claim", source_refs=[evidence["id"]])
        with self.assertRaises(ValueError):
            self.store.remember("a", "fabricated source", kind="source")

    def test_inference_without_evidence_is_explicitly_unreferenced(self):
        record = self.store.remember("a", "The pool might be saturated.", kind="inference")
        self.assertEqual(record["kind"], "inference")
        self.assertEqual(record["source_refs"], [])
        self.assertEqual(record["evidence_status"], "unreferenced")

    def test_forget_removes_fts_and_vector_rows(self):
        self.store.embedder = LocalEmbeddings()
        record = self.store.remember("a", "backup orchard")
        self.assertEqual(len(self.hits("orchard")), 1)
        self.assertFalse(self.store.forget("b", record["id"]))
        self.assertTrue(self.store.forget("a", record["id"]))
        self.assertEqual(self.hits("orchard"), [])
        with closing(sqlite3.connect(self.db)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM embeddings").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM records_fts WHERE records_fts MATCH 'orchard'").fetchone()[0], 0)

    def test_hybrid_search_and_changed_model_fingerprint(self):
        encoder = LocalEmbeddings()
        self.store.embedder = encoder
        self.write(self.a / "policy.md", "backup each week")
        self.store.ingest("a")
        answer = self.store.search("a", "backup", mode="hybrid")
        self.assertEqual(answer["mode"], "hybrid")
        self.assertEqual(len(answer["results"]), 1)
        encoder.identity = "test-model-v2"
        answer = self.store.search("a", "backup", mode="hybrid")
        self.assertEqual(answer["mode"], "lexical")
        self.assertTrue(answer["warnings"])
        self.store.ingest("a")
        self.assertEqual(self.store.search("a", "backup", mode="hybrid")["mode"], "hybrid")

    def test_embedding_failure_keeps_lexical_evidence_and_warns(self):
        encoder = LocalEmbeddings()
        encoder.fail = True
        self.store.embedder = encoder
        self.write(self.a / "policy.md", "backup each week")
        result = self.store.ingest("a")
        self.assertTrue(result["warnings"])
        answer = self.store.search("a", "backup", mode="hybrid")
        self.assertEqual(answer["mode"], "lexical")
        self.assertEqual(len(answer["results"]), 1)
        self.assertTrue(answer["warnings"])

    def test_literal_query_does_not_enable_fts_operators(self):
        self.store.remember("a", "ordinary orchard text")
        self.assertEqual(self.hits('" OR * : - ()'), [])
        self.assertEqual(len(self.hits('orchard "')), 1)

    def test_persistence_survives_reopening(self):
        self.store.remember("a", "persistent orchard")
        with KnowledgeStore(self.db, {"a": str(self.a)}) as reopened:
            self.assertEqual(len(reopened.search("a", "orchard")["results"]), 1)

    def test_changed_evidence_marks_an_existing_decision_reference_stale(self):
        source = self.a / "policy.md"
        self.write(source, "weekly backup")
        self.store.ingest("a")
        evidence = self.hits("backup")[0]
        self.store.remember("a", "retain weekly policy", kind="decision", source_refs=[evidence["id"]])
        self.write(source, "daily backup")
        self.store.ingest("a")
        result = self.hits("retain")[0]
        self.assertEqual(result["evidence_status"], "stale_references")
        self.assertEqual(result["source_refs"][0]["source_sha256"], evidence["source_sha256"])

    def test_rewritten_source_keeps_exact_bytes_and_refreshes_citation(self):
        source = self.a / "windows.md"
        source.write_bytes(b"old evidence\r\n")
        self.assertEqual(self.store.ingest("a")["added"], 1)
        replacement = b"new orchard evidence\r\nsecond line\r\n"
        source.write_bytes(replacement)
        self.assertEqual(self.store.ingest("a")["updated"], 1)
        hit = self.hits("orchard")[0]
        self.assertEqual(hit["source_sha256"], hashlib.sha256(replacement).hexdigest())
        self.assertEqual(hit["text"], replacement.decode())
        self.assertEqual(hit["start_line"], 1)
        self.assertEqual(hit["end_line"], 2)

    def test_source_mutated_during_read_is_still_rejected(self):
        import os
        from types import SimpleNamespace
        source = self.a / "race.md"
        source.write_bytes(b"orchard")
        real_fstat = os.fstat
        calls = 0
        def changed(fd):
            nonlocal calls
            calls += 1
            result = real_fstat(fd)
            if calls == 2:
                attrs = {name: getattr(result, name) for name in
                         ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}
                attrs["st_mtime_ns"] += 1
                return SimpleNamespace(**attrs)
            return result
        with patch("homelab.knowledge.os.fstat", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed during read"):
                self.store._read_source("a", "race.md")

    def test_corrupt_database_is_not_overwritten(self):
        corrupt = self.base / "corrupt.db"
        original = b"not a sqlite database"
        corrupt.write_bytes(original)
        with self.assertRaises(sqlite3.DatabaseError):
            KnowledgeStore(corrupt, {})
        self.assertEqual(corrupt.read_bytes(), original)
        corrupt.unlink()  # Constructor failure must release its Windows handle.

    def test_failed_initialization_closes_database_connection(self):
        corrupt = self.base / "failed-init.db"
        corrupt.write_bytes(b"not a sqlite database")
        # Retain the incomplete instance so the real connection can be inspected
        # without replacing sqlite3 or depending on garbage-collection timing.
        incomplete = KnowledgeStore.__new__(KnowledgeStore)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                incomplete.__init__(corrupt, {})
            with self.assertRaises(sqlite3.ProgrammingError):
                incomplete._db.cursor()
        finally:
            incomplete.close()

    def test_authored_notes_have_a_total_byte_cap(self):
        with patch("homelab.knowledge.MAX_NOTE_BYTES", 20, create=True):
            self.store.remember("a", "first note text")
            with self.assertRaises(ValueError):
                self.store.remember("a", "second note text")
        self.assertEqual(self.store.status("a")["records"], 1)

    def test_rebuild_restores_derived_fts_without_losing_notes(self):
        self.store.remember("a", "persistent orchard")
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute("INSERT INTO records_fts(records_fts) VALUES('delete-all')")
        self.assertEqual(self.hits("orchard"), [])
        self.assertTrue(callable(getattr(self.store, "rebuild_index", None)), "Index rebuild is not implemented")
        self.store.rebuild_index("a")
        self.assertEqual(len(self.hits("orchard")), 1)

    def test_long_line_chunks_are_bounded_and_keep_offsets(self):
        text = "orchard " * 800 + "\nsecond line\n"
        self.write(self.a / "long.md", text)
        self.store.ingest("a")
        hits = self.hits("orchard", limit=50)
        self.assertGreater(len(hits), 1)
        for row in hits:
            self.assertEqual(row["text"], text[row["start_char"]:row["end_char"]])
            self.assertLessEqual(len(row["text"]), 1600)


if __name__ == "__main__":
    unittest.main()
