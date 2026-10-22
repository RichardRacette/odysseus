# Project knowledge and evidence

The home-lab companion adds a project evidence store beside Odysseus's existing
personal memory and Chroma document retrieval. It does not discover, automatically read, or migrate
`memory.json`, conversations, uploads, account settings, or any other existing
Odysseus data. Only explicitly approved project files are eligible. Enable a project by selecting its directory, inspect
the scan plan, and then ingest it.

The core uses Python 3.10+ and SQLite with FTS5. Embeddings are optional and off
unless an `OllamaEmbedder` is supplied. No constructor scans a project, downloads
a model, or contacts an endpoint.

## Evidence contract

Search always names one exact configured project. Each source result contains:

| Field | Meaning |
| --- | --- |
| `id` | This particular chunk occurrence; equal text in different files has different IDs. |
| `project`, `source_path`, `root_id` | Project, relative source path, and fingerprint of the approved root. |
| `source_sha256` | SHA256 of the original source bytes, including original line endings. |
| `chunk_sha256` | SHA256 of the exact stored UTF-8 chunk text. |
| `start_line`, `end_line` | Inclusive, one-based source line range. |
| `start_char`, `end_char` | Zero-based character offsets; the end offset is exclusive. |
| `citation` | Printable project/path/line/hash reference. |
| `kind` | `source`, `note`, `inference`, or `decision`. |
| `evidence_status` | Source, unreferenced record, referenced record, or stale references. |
| `untrusted_content` | Always true: retrieved text is evidence to inspect, not executable instructions. |

Text is preserved rather than normalized into new sentences. Chunks contain at
most 1,600 characters; splitting prefers a nearby newline and handles very long
lines with exact offsets. CRLF and LF sources retain their original text.
There is no OCR, PDF, Office, image, URL-fetching, or executable-document adapter
in this version. Unsupported file types are reported in the scan exclusions.

A `source` label means the text came from the specified file. It does not mean
the file's claims are true. Similarity and ranking scores are retrieval signals,
not confidence or truth probabilities.

## Approved directories and bounds

`KnowledgeStore(db_path, projects, embedder=None)` accepts a mapping from simple
project identifiers to canonical absolute directory paths. An empty mapping is
valid and provides no ingestion authority. Roots cannot overlap, be a filesystem
root, be hidden themselves, or contain a symlink/junction ancestor. Configuration
commands resolve an operator-selected path before recording it. For direct API
use on macOS, use `Path(path).resolve()` for standard `/tmp` and `/var` aliases;
the persisted approved directory is the canonical `/private/...` location.

The scanner excludes hidden paths, dependency/generated directories, private-key
extensions, and filename patterns associated with credentials, tokens,
passwords, secrets and API keys. It excludes symlink files and directories.
The supported text allowlist includes Markdown, text, reStructuredText, JSON,
Python, JavaScript/TypeScript, YAML, TOML, CSV, SQL, HTML/CSS, shell/PowerShell,
INI and CFG files. The source reader rechecks confinement and regular-file
identity; POSIX reads use directory descriptors and `O_NOFOLLOW` for each path
component. Windows uses canonical-path and file-identity checks; physical
Windows reparse-point and concurrent filesystem behavior still need host tests.

These exclusions do not detect every secret embedded inside an otherwise
permitted document. Select a deliberately curated project directory and inspect
the plan. Do not approve a whole home directory or a folder containing private
configuration simply because it has a familiar project name.

| Bound | Limit |
| --- | --- |
| Individual source file | 1 MiB |
| Eligible source files per project scan | 1,000 |
| Eligible source bytes per project scan | 32 MiB |
| Files/directories encountered per scan | 20,000 |
| Records per project, including source chunks and authored records | 10,000 |
| Authored note/inference/decision text per project | 8 MiB total |
| Individual authored record | 16,000 characters |
| References on one authored record | 32 |
| Search query | 2,000 characters |
| Returned search results | 1–50 |

`scan(project)` returns relative `selected` paths, `selected_count`,
`selected_bytes`, exclusions and counts, errors, and `complete`. An excluded
directory counts as one excluded entry; the scanner does not count all files
inside a deliberately unvisited subtree. Exclusion details are capped at 1,000
entries, while `excluded_count` retains the encountered count. Scanning is a
dry run: it reads file metadata, not document contents, and writes no source
records. Exceeding a corpus or traversal limit produces an incomplete plan.

## Ingest, updates and stale evidence

`ingest(project)` returns source counts (`added`, `updated`, `unchanged`,
`removed`), chunk counts, errors, and warnings. It hashes file bytes and skips
unchanged sources. Changed sources replace their old records and vector rows
inside a transaction. Separate source paths remain separate even when their
text is identical.

A complete scan establishes which previously indexed paths disappeared or
became excluded. Those source records and their derived entries are deleted.
An incomplete scan does not authorize deletion: prior sources are marked stale
and withheld from retrieval. This protects against treating an offline drive or
failed traversal as a deliberate corpus deletion.

A file that cannot be read as bounded UTF-8 text is reported as an error. Its
prior source is marked stale. Successful future ingestion can restore its
current status. Search also rereads and hashes each candidate source before
returning it. If the source changed after the last ingest, disappeared, became
unsafe, or cannot be read, that result is withheld with a warning. This check is
performed at retrieval time; a file can naturally change again after the
response is returned, so the recorded hash identifies exactly which version
the citation refers to.

`status(project=None)` reports counts for configured projects. Its stale count
reflects the last ingestion state; search-time freshness checks also appear in
the search response's warnings. Status does not contact Ollama or enumerate
source contents.

## Notes, inferences and decisions

`remember(project, text, kind="note", source_refs=None)` creates an explicit
authored record. Allowed authored kinds are `note`, `inference`, and `decision`;
only file ingestion can create `source` records. Unreferenced records retain
`evidence_status: "unreferenced"`.

References are same-project record IDs, either plain strings or objects with a
`record_id` key. They are resolved by the store, so a caller cannot attach a
fabricated citation by merely supplying a path and hash. Each reference stores
an immutable citation snapshot. If the referenced record later disappears or
its source becomes stale, the authored record remains available with
`evidence_status: "stale_references"`. Its original references remain visible.
Reference status describes availability and version continuity, not validation
of the authored conclusion.

`forget(project, record_id)` deletes one exact record only within its project.
Vector rows cascade away and FTS postings are rebuilt. This removes active
retrieval results. The original file and old backup snapshots are not erased.
A later changed-file ingestion may recreate a forgotten source chunk; remove
the source from the approved corpus if it must remain excluded. There is no
automatic model-driven extraction or promotion of guesses into verified facts.

## Lexical and semantic search

`search(project, query, limit=5, mode="lexical")` returns:

```json
{"results": [], "mode": "lexical", "warnings": []}
```

Lexical search uses SQLite FTS5 and treats input words literally rather than
accepting user-supplied FTS operators. Hybrid search combines lexical and exact
cosine-vector rankings with reciprocal rank fusion. It scans compatible vectors
for the specified project, so it has linear vector-search cost. No throughput
or latency target has been measured on the physical lab. The corpus caps keep
this first version bounded; measure real queries before adding an ANN service.

If no embedder is configured, hybrid search explicitly falls back to lexical.
If Ollama fails, a model fingerprint changes, vectors have incompatible
dimensions, or only part of the corpus was embedded, the response reports that
condition. Lexical records remain usable. Repeating `ingest` after recovery
fills missing embeddings even when source files are unchanged.

### Optional local Ollama embeddings

```python
from homelab.embeddings import OllamaEmbedder

embedder = OllamaEmbedder(
    "http://127.0.0.1:11434",
    model="embeddinggemma",
    timeout=30,
)
```

This client checks positive-size local weights in `/api/tags`, refreshes their
digest, and inspects `/api/show` for remote execution metadata before using
`/api/embed` with `truncate: false`. It accepts literal loopback endpoints
and normalizes `localhost` to `127.0.0.1`; it rejects remote hosts, embedded
credentials, redirects, and cloud-model names or inventory entries. Environment
and system proxy settings are ignored. If local inventory cannot be verified,
no document text is sent and the caller receives lexical fallback. If an
otherwise valid local inventory entry lacks a digest, an explicitly unverified
fingerprint and warning are used.

The fingerprint includes model identity/digest and text preprocessing version.
The original `embeddinggemma` model uses Google's query and document retrieval
prefixes. Other explicitly configured local models use raw text; evaluate their
prompt requirements before choosing one. Vectors must have a consistent finite,
nonzero dimension and are normalized before storage/comparison. Changing the
model or preprocessing cannot silently reuse incompatible vectors.

The client never pulls a model. Install and validate an embedding model
separately before enabling it. A loopback endpoint still relies on the trusted
local Ollama configuration; the deployment checklist must also verify Ollama's
local-only settings. Real-model retrieval quality and performance remain
unverified until the physical lab evaluation is run.

## Direct API example

Run from `integrations/homelab`, or add that directory to `PYTHONPATH`:

```python
from pathlib import Path
from homelab.knowledge import KnowledgeStore

root = Path("/absolute/path/to/curated-project").resolve()
database = Path("/absolute/path/to/local-state/knowledge.sqlite3")

with KnowledgeStore(database, {"lab": str(root)}) as store:
    print(store.scan("lab"))       # Inspect the selection before ingestion.
    print(store.ingest("lab"))
    answer = store.search("lab", "backup policy")
    print(answer)
    if answer["results"]:
        decision = store.remember(
            "lab", "Use the documented backup schedule.", kind="decision",
            source_refs=[answer["results"][0]["id"]],
        )
        print(decision)
```

The optional read-only MCP adapter and hub enforce their configured project
before calling this API. Root approval, ingestion, record creation and deletion
remain administrative/CLI operations. Existing Odysseus Codex integration
permissions are not bypassed to access personal data.

## Recovery and rebuilds

The database contains authored records and source-text snapshots as well as
derived FTS/vector indexes. Preserve it with the companion's SQLite snapshot
and restore commands; keep original approved source files in their own backup.
Never share a live WAL database through a network drive or synchronized folder.

`rebuild_index(project)` reconstructs FTS from retained records and clears and
rebuilds that project's optional embeddings. SQLite's FTS rebuild operates on
the shared derived index; it does not change source records or project
permissions. This operation reads stored text rather than refreshing source
files. Use `ingest(project)` to reconcile the current filesystem. A vector
rebuild that fails reports warnings and leaves lexical retrieval usable.

Do not delete the entire database to repair an index: authored decisions and
notes live there. A corrupt database is reported rather than silently replaced.
Backup restore, FTS rebuild, and ingestion are distinct operations.

## Verification and source references

The focused tests use real temporary SQLite databases and a loopback synthetic
Ollama HTTP server. They cover isolation, exact citations, updates/removals,
stale sources, exclusions, symlinks, input bounds, persistent notes, vector
validation, proxy/redirect policy, local-model inventory and visible fallback.
They do not download models or read private files.

Official implementation references, checked 2026-10-22:

- [Ollama native embeddings API](https://docs.ollama.com/api/embed)
- [Ollama embedding guidance](https://docs.ollama.com/capabilities/embeddings)
- [Ollama local model inventory](https://docs.ollama.com/api/tags)
- [Original EmbeddingGemma model card and retrieval prompts](https://ai.google.dev/gemma/docs/embeddinggemma/model_card)
- [SQLite FTS5, external content, deletion and rebuild](https://www.sqlite.org/fts5.html)
- [SQLite WAL and its single-host requirement](https://sqlite.org/wal.html)
- [Python SQLite online backup interface](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)
