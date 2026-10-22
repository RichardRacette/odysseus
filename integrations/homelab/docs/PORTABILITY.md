# Windows portability corrections

The first cross-platform CI run exposed Windows failures in source freshness and snapshot publication, plus a macOS demo subprocess timeout. These corrections are confined to the companion.

- Source reads compare device, inode, size and modification time against the current path. They still compare all timestamps before/after reading the open handle. On Windows, handle and path `ctime` can have different meanings; comparing those two values rejected unchanged or freshly rewritten files. Symlink/junction rejection and mutation checks remain enforced.
- Snapshot final flushing opens the completed database for read/write. Windows rejects `fsync` on a read-only file descriptor.
- Failed knowledge database initialization closes the connection immediately, including on a corrupt database. Test-only SQLite connections now close explicitly; a SQLite transaction context does not itself close a connection.
- Text fixtures specify LF when their expected hashes are calculated from LF bytes. A separate CRLF regression checks the original file's exact bytes, hash and line span.
- The complete synthetic CLI demonstration has a bounded 60-second test budget. Other CLI checks retain their 20-second budget. A timeout remains a failure.

Regression coverage exercises rewritten CRLF files, mutations during a read, writable snapshot flushing and release of a corrupt database handle. Run the full suite and the separate demonstration from the companion directory. Install optional MCP dependencies in a separate environment to include the actual protocol test.

```text
python -m unittest discover -s tests -v
python -m compileall -q homelab lab.py
python lab.py demo --output NEW_PRIVATE_DIRECTORY_OUTSIDE_GIT
```

Actual host receipts and model outputs belong outside Git. A local passing run does not establish a passing macOS CI rerun or physical multi-host acceptance.

References: [Python stat timestamps](https://docs.python.org/3/library/os.html#os.stat_result.st_ctime), [SQLite connection contexts](https://docs.python.org/3/library/sqlite3.html#how-to-use-the-connection-context-manager).
