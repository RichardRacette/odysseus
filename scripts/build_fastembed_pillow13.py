#!/usr/bin/env python3
"""Build an opt-in FastEmbed 0.9.0 metadata backport; never edit app requirements."""

from __future__ import annotations

import argparse
import difflib
import email
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import venv
import zipfile


VERSION = "0.9.0+odysseus.pillow13.1"
SOURCE_URL = (
    "https://files.pythonhosted.org/packages/cc/96/"
    "d7d9d4c8860cec4ee4c26a0315ad9bb9fc5d0c676450b194f2478e202941/"
    "fastembed-0.9.0.tar.gz"
)
SOURCE_SHA256 = "bc3beadb46ecb3580ab832d12670be7ecb937f80adfcb7b77b03f7eef76c394a"
RELEASE_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/84/bc/"
    "21791fa8b16c6f5f8e2717f8defab377e74c1ccc8687180b7224907e7641/"
    "fastembed-0.9.0-py3-none-any.whl"
)
RELEASE_WHEEL_SHA256 = "273d408edec8c0f161711d8f6e44e4a5b559d18e8edf6bf805415d55dc772846"
PYPROJECT_SHA256 = "99058a8605189a7a8a25767806d836719178d439bf48e5cde6040901ae0313a0"
WHEEL_SHA256 = "814bbfe324a803ab0aaa13f137516df26bf4a28bcc847d87a953006475d5b05c"
BUILD_REQUIREMENTS = ("build==1.6.1", "poetry-core==2.5.0")
SOURCE_DATE_EPOCH = "1704067200"  # Stable ZIP timestamps, 2024-01-01 UTC.


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_bytes(data: bytes, expected: str) -> bytes:
    actual = sha256(data)
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch: expected {expected}, got {actual}")
    return data


def fetch(url: str, expected: str, local_path: Path | None = None) -> bytes:
    if local_path is not None:
        return checked_bytes(local_path.read_bytes(), expected)
    with urllib.request.urlopen(url, timeout=60) as response:
        return checked_bytes(response.read(), expected)


def extract_source(data: bytes, destination: Path) -> dict[str, bytes]:
    """Extract regular files only, rejecting links, escapes, and duplicate names."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != "fastembed-0.9.0"
                or "\\" in member.name
            ):
                raise ValueError(f"Unsafe source archive path: {member.name}")
            if member.isdir():
                continue
            if not member.isfile() or len(path.parts) < 2:
                raise ValueError(f"Unsupported source archive entry: {member.name}")
            relative = str(PurePosixPath(*path.parts[1:]))
            if relative in files:
                raise ValueError(f"Duplicate source archive entry: {member.name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Cannot read source archive entry: {member.name}")
            files[relative] = stream.read()
    # All entries are checked before any files are written.
    for relative, contents in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    return files


def patch_metadata(original: bytes) -> bytes:
    checked_bytes(original, PYPROJECT_SHA256)
    text = original.decode("utf-8")
    if text.count('version = "0.9.0"') != 1 or text.count("<13.0") != 4:
        raise ValueError("Unexpected release metadata; refusing to patch")
    text = text.replace('version = "0.9.0"', f'version = "{VERSION}"', 1)
    # The three runtime markers and docs group retain their existing lower bounds.
    return text.replace("<13.0", "<14.0").encode("utf-8")


def wheel_contents(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate wheel entries")
        return {name: archive.read(name) for name in names if not name.endswith("/")}


def verify_wheel(
    wheel: bytes, release_wheel: bytes, source_files: dict[str, bytes]
) -> dict[str, str]:
    actual = wheel_contents(wheel)
    released = wheel_contents(release_wheel)
    expected_members = {
        name.replace("fastembed-0.9.0.dist-info/", f"fastembed-{VERSION}.dist-info/", 1)
        for name in released
    }
    if set(actual) != expected_members:
        raise ValueError("Unexpected wheel member set")
    source_runtime = {n: data for n, data in source_files.items() if n.startswith("fastembed/")}
    release_runtime = {n: data for n, data in released.items() if n.startswith("fastembed/")}
    built_runtime = {n: data for n, data in actual.items() if n.startswith("fastembed/")}
    if not source_runtime or source_runtime != release_runtime or built_runtime != release_runtime:
        raise ValueError("Runtime files differ from the published 0.9.0 wheel")
    for name in ("LICENSE", "NOTICE"):
        contents = source_files[name]
        if actual.get(f"fastembed-{VERSION}.dist-info/licenses/{name}") != contents:
            raise ValueError(f"Built wheel did not preserve {name}")
        if released.get(f"fastembed-0.9.0.dist-info/licenses/{name}") != contents:
            raise ValueError(f"Published wheel and source disagree on {name}")
    metadata = email.message_from_bytes(actual[f"fastembed-{VERSION}.dist-info/METADATA"])
    release_metadata = email.message_from_bytes(released["fastembed-0.9.0.dist-info/METADATA"])
    if metadata["Name"] != "fastembed" or metadata["Version"] != VERSION:
        raise ValueError("Incorrect derived distribution identity")
    expected_requires = sorted(
        value.replace("<13.0", "<14.0") if value.lower().startswith("pillow ") else value
        for value in release_metadata.get_all("Requires-Dist", [])
    )
    if sorted(metadata.get_all("Requires-Dist", [])) != expected_requires:
        raise ValueError("Unexpected runtime dependency metadata delta")
    if metadata["Requires-Python"] != release_metadata["Requires-Python"]:
        raise ValueError("Unexpected Python support change")
    return {name: sha256(contents) for name, contents in sorted(built_runtime.items())}


def requirements_text(app_requirements: Path, wheel_path: Path, digest: str) -> str:
    return (
        "# Opt-in local build; regenerate this file if the wheel or checkout moves.\n"
        f"-r {app_requirements.resolve().as_uri()}\n"
        f"fastembed @ {wheel_path.resolve().as_uri()}#sha256={digest}\n"
        "Pillow>=13.0.0,<14\n"
    )


def build(output: Path, app_root: Path, source_archive: Path | None, release_wheel: Path | None) -> None:
    if sys.version_info < (3, 11):
        raise ValueError("Run this builder with Python 3.11 or newer")
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be empty; use a new directory for each build")
    app_requirements = app_root.resolve() / "requirements.txt"
    if not app_requirements.is_file():
        raise ValueError("Application root must contain requirements.txt")
    source = fetch(SOURCE_URL, SOURCE_SHA256, source_archive)
    published_wheel = fetch(RELEASE_WHEEL_URL, RELEASE_WHEEL_SHA256, release_wheel)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fastembed-pillow13-") as temporary:
        work = Path(temporary)
        tree = work / "source"
        files = extract_source(source, tree)
        patched = patch_metadata(files["pyproject.toml"])
        (tree / "pyproject.toml").write_bytes(patched)
        patch = "".join(difflib.unified_diff(
            files["pyproject.toml"].decode().splitlines(keepends=True),
            patched.decode().splitlines(keepends=True),
            fromfile="a/pyproject.toml", tofile="b/pyproject.toml",
        ))
        environment = work / "build-venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", *BUILD_REQUIREMENTS], check=True)
        env = dict(os.environ, SOURCE_DATE_EPOCH=SOURCE_DATE_EPOCH)
        # --no-isolation uses only the disposable, fully provisioned build venv above.
        # The build frontend still checks all declared build dependencies.
        staging = work / "dist"
        subprocess.run(
            [str(python), "-m", "build", "--wheel", "--no-isolation", "--outdir", str(staging), str(tree)],
            env=env, check=True,
        )
        wheel_path = output / f"fastembed-{VERSION}-py3-none-any.whl"
        wheel_bytes = (staging / wheel_path.name).read_bytes()
        runtime_hashes = verify_wheel(wheel_bytes, published_wheel, files)
        checked_bytes(wheel_bytes, WHEEL_SHA256)
    # Publish artifacts only after source, metadata, license, and wheel checks pass.
    wheel_path.write_bytes(wheel_bytes)
    digest = sha256(wheel_bytes)
    (output / "metadata.patch").write_text(patch, encoding="utf-8")
    requirements = requirements_text(app_requirements, wheel_path, digest)
    (output / "requirements-backport.txt").write_text(requirements, encoding="utf-8")
    provenance = {
        "status": "experimental opt-in metadata backport; not an upstream release",
        "version": VERSION,
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "release_wheel_url": RELEASE_WHEEL_URL,
        "release_wheel_sha256": RELEASE_WHEEL_SHA256,
        "wheel_filename": wheel_path.name,
        "wheel_sha256": digest,
        "build_requirements": list(BUILD_REQUIREMENTS),
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "metadata_patch_sha256": sha256(patch.encode()),
        "runtime_files_identical_to_release": True,
        "runtime_file_sha256": runtime_hashes,
        "license_sha256": {name: sha256(files[name]) for name in ("LICENSE", "NOTICE")},
        "application_requirements_sha256": sha256(app_requirements.read_bytes()),
        "application_requirements_changed": False,
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Built {wheel_path.name}\nSHA-256: {digest}\nRuntime files unchanged: {len(runtime_hashes)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/fastembed-pillow13"))
    parser.add_argument("--application-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-archive", type=Path, help="Optional offline copy; hash is always checked")
    parser.add_argument("--release-wheel", type=Path, help="Optional official wheel; hash is always checked")
    args = parser.parse_args()
    build(args.output_dir, args.application_root, args.source_archive, args.release_wheel)


if __name__ == "__main__":
    main()
