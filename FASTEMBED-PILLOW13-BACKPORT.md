# Opt-in FastEmbed / Pillow 13 build

FastEmbed 0.9.0 caps Pillow below 13, while the current Pillow advisories are
fixed in 13.0.0. The opt-in builder creates
`fastembed-0.9.0+odysseus.pillow13.1` from the published 0.9.0 source. It changes
only the distribution version and Pillow's upper bounds in `pyproject.toml`.
The generated application requirements also require `Pillow>=13.0.0,<14`.

This is an experimental, locally maintained metadata backport. Running the
builder does not change `requirements.txt`, Docker builds, application settings,
or CI. Adoption and artifact distribution need a separate maintenance decision.
An unmerged upstream source pin was evaluated separately and is not used here.

## Build and inspect

Run from the repository root with Python 3.11 or newer:

```sh
python scripts/build_fastembed_pillow13.py
```

The default output directory, `dist/fastembed-pillow13`, is already ignored by
Git. It must be empty. Use `--output-dir` for an additional independent build.
The script downloads two immutable PyPI artifacts, checks their SHA-256 hashes,
creates a disposable build environment with `build==1.6.1` and
`poetry-core==2.5.0`, and runs the standard wheel build. It then verifies:

- Every package file is byte-identical to the official release wheel (67 files).
- `LICENSE` and `NOTICE` are unchanged and present in the derived wheel.
- The wheel contains the same members after renaming its `dist-info` directory.
- Runtime dependency metadata changes only Pillow's upper bounds; Python support
  and other dependencies stay unchanged.
- The complete wheel matches the expected hash below.

Artifacts are copied to the output directory only after these checks pass.
The directory contains the wheel, `metadata.patch`, `provenance.json`, and
`requirements-backport.txt`. The latter uses absolute file URLs with a wheel
hash. Regenerate it after moving the output or checkout. It includes the existing
application requirements and adds the exact derived wheel and Pillow bound.

| Artifact | SHA-256 |
|---|---|
| Official `fastembed-0.9.0.tar.gz` | `bc3beadb46ecb3580ab832d12670be7ecb937f80adfcb7b77b03f7eef76c394a` |
| Official `fastembed-0.9.0-py3-none-any.whl` | `273d408edec8c0f161711d8f6e44e4a5b559d18e8edf6bf805415d55dc772846` |
| Derived `fastembed-0.9.0+odysseus.pillow13.1-py3-none-any.whl` | `814bbfe324a803ab0aaa13f137516df26bf4a28bcc847d87a953006475d5b05c` |

The official artifact URLs are constants in the builder and are repeated in its
provenance receipt. `--source-archive` and `--release-wheel` accept downloaded
copies; the same hash checks apply. Build-tool installation still requires a
package index or a configured package cache.

## Isolated evaluation

The following commands use a new environment; ordinary pip dependency resolution
remains enabled:

```sh
python3.14 -m venv /tmp/odysseus-pillow13-evaluation
/tmp/odysseus-pillow13-evaluation/bin/python -m pip install -r dist/fastembed-pillow13/requirements-backport.txt
/tmp/odysseus-pillow13-evaluation/bin/python -m pip check
/tmp/odysseus-pillow13-evaluation/bin/python -m pip install pip-audit==2.10.0
/tmp/odysseus-pillow13-evaluation/bin/python -m pip_audit -r dist/fastembed-pillow13/requirements-backport.txt -r requirements-optional.txt --strict
```

**The last command currently fails:** pip-audit's default PyPI service cannot
audit the unpublished local version `0.9.0+odysseus.pillow13.1`. That failure must
remain visible; this tooling does not make the existing audit gate pass.

The supported OSV service can additionally audit the full resolved dependency
set without dropping packages or ignoring advisories:

```sh
/tmp/odysseus-pillow13-evaluation/bin/python -m pip_audit -r dist/fastembed-pillow13/requirements-backport.txt -r requirements-optional.txt --strict --vulnerability-service osv
```

An OSV result for this local version is supplemental evidence. The package is
not an upstream release, and a version-based service does not review its source.
Retain the byte-identity proof and the published 0.9.0 audit evidence alongside
the generated wheel. The repository's existing audit workflow remains unchanged.

## Validation and limits

The builder's offline integrity tests run with the ordinary application suite:

```sh
python -m pytest -q tests/test_fastembed_backport_builder.py
```

Local validation produced the same wheel hash on Linux with Python 3.11, 3.12,
and 3.14. Python 3.11 and 3.14 normal installation, `pip check`, 45 focused
embedding and gallery tests, and real default MiniLM inference passed. All 18
builder integrity tests passed on both runtimes; 11 upstream image-transform
tests additionally passed on 3.14. Strict OSV core-plus-optional audits reported
no known vulnerabilities or skipped packages (194 packages on 3.11; 122 on 3.14).
This does not establish native Windows/macOS support, every configurable model's
behavior, or a clean full application/network integration run. Full-suite local
attempts were interrupted by automatic network approval review; they have no
complete pass/fail result.

For local model validation, disable ONNX Runtime telemetry before Python starts
with `ORT_DISABLE_TELEMETRY=1`, and Hugging Face telemetry with
`HF_HUB_DISABLE_TELEMETRY=1`. `HF_HUB_OFFLINE=1` uses a model cache that is already
populated. Test fixtures that contact external services need an explicit local
network guard or a separately authorized integration environment.

Remove this workaround when a reviewed upstream release permits the fixed
Pillow version and passes the application's compatibility and audit checks.

Sources: [FastEmbed 0.9.0 metadata](https://pypi.org/pypi/fastembed/0.9.0/json),
[Pillow 13 release notes](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html),
[pip-audit services](https://github.com/pypa/pip-audit), and
[ONNX Runtime privacy controls](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md).

## Hosted candidate verification (not adoption)

The read-only [Pillow 13 candidate validation workflow](.github/workflows/pillow13-candidate.yml)
provides a distinct, opt-in evaluation of this toolchain on hosted Linux
Python 3.11 and 3.14. It rebuilds the exact hash-pinned wheel from the published
FastEmbed 0.9.0 artifacts, installs it with regular pip dependency resolution,
checks the installed local version and Pillow major version, runs integrity and
focused embedding/image regressions, and audits the core-plus-optional resolved
candidate graph with strict OSV. The Python 3.11 job additionally runs the full
application pytest suite. No wheel is published or deployed, and it never
changes the application's default requirements or existing security gate.

A passing candidate workflow is **not** an upstream vulnerability fix, a
passing default PyPI audit, a supported Windows/macOS rollout, proof of live
model/device interoperability, or authorization to change production dependencies.
The built wheel is a local derivative. Version-based OSV results do not inspect
its source, which is why the wheel and runtime-file hashes are verified separately.

Before considering adoption, retain the exact CI run/commit and provenance,
review maintenance ownership and distribution strategy, evaluate real application
model inference and cross-platform behavior, and secure approval for a separate
default-dependency change with a rollback procedure. Re-run **all** normal CI
and security checks on that exact dependency-change head. Do not use
`--no-deps`, bypass GitHub dependency review, suppress Pillow advisories, or
interpret an advisory job as green when it fails. The default `pip-audit`
remains red until normal dependency resolution can install a fixed Pillow.
