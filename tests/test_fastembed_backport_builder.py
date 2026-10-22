"""Integrity boundaries for the opt-in dependency backport builder; no network."""

import importlib.util
import io
from pathlib import Path
import tarfile
import zipfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_fastembed_pillow13.py"
spec = importlib.util.spec_from_file_location("fastembed_backport_builder", SCRIPT)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def archive(entries):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as output:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 1 if kind == tarfile.REGTYPE else 0
            member.linkname = "outside"
            output.addfile(member, io.BytesIO(b"x") if member.size else None)
    return data.getvalue()


@pytest.mark.parametrize("name,kind", [
    ("../outside", tarfile.REGTYPE),
    ("/fastembed-0.9.0/outside", tarfile.REGTYPE),
    ("fastembed-0.9.0/../outside", tarfile.REGTYPE),
    ("other-root/file", tarfile.REGTYPE),
    ("fastembed-0.9.0/dir\\outside", tarfile.REGTYPE),
    ("fastembed-0.9.0/link", tarfile.SYMTYPE),
    ("fastembed-0.9.0/link", tarfile.LNKTYPE),
    ("fastembed-0.9.0/device", tarfile.CHRTYPE),
])
def test_unsafe_archives_write_nothing(tmp_path, name, kind):
    content = archive([("fastembed-0.9.0/valid", tarfile.REGTYPE), (name, kind)])
    with pytest.raises(ValueError):
        builder.extract_source(content, tmp_path)
    assert not list(tmp_path.iterdir())


def test_duplicate_archive_paths_are_rejected(tmp_path):
    content = archive([("fastembed-0.9.0/file", tarfile.REGTYPE)] * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        builder.extract_source(content, tmp_path)
    assert not list(tmp_path.iterdir())


def test_offline_archive_is_hash_checked_before_use(tmp_path):
    path = tmp_path / "source.tar.gz"
    path.write_bytes(b"modified source")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        builder.fetch(builder.SOURCE_URL, builder.SOURCE_SHA256, path)


def test_unexpected_release_metadata_is_rejected():
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        builder.patch_metadata(b'version = "0.9.0"\npillow = "<13.0"\n')


def wheel(version, runtime=b"released runtime\n", license_text=b"license", dependency="pillow (>=12,<13.0)"):
    files = {
        "fastembed/__init__.py": runtime,
        f"fastembed-{version}.dist-info/licenses/LICENSE": license_text,
        f"fastembed-{version}.dist-info/licenses/NOTICE": b"notice",
        f"fastembed-{version}.dist-info/METADATA": (
            f"Name: fastembed\nVersion: {version}\nRequires-Python: >=3.10\n"
            f"Requires-Dist: {dependency}\nRequires-Dist: numpy (>=2.3)\n\n"
        ).encode(),
    }
    data = io.BytesIO()
    with zipfile.ZipFile(data, mode="w") as output:
        for name, value in files.items():
            output.writestr(name, value)
    return data.getvalue()


def source_files():
    return {"fastembed/__init__.py": b"released runtime\n", "LICENSE": b"license", "NOTICE": b"notice"}


def test_only_metadata_delta_preserves_runtime_and_license():
    hashes = builder.verify_wheel(
        wheel(builder.VERSION, dependency="pillow (>=12,<14.0)"), wheel("0.9.0"), source_files()
    )
    assert hashes == {"fastembed/__init__.py": builder.sha256(b"released runtime\n")}


@pytest.mark.parametrize("changes,reason", [
    ({"runtime": b"modified runtime"}, "Runtime files differ"),
    ({"license_text": b"replaced license"}, "preserve LICENSE"),
    ({"dependency": "pillow (>=12,<15.0)"}, "dependency metadata delta"),
])
def test_unexpected_wheel_changes_are_rejected(changes, reason):
    options = {"dependency": "pillow (>=12,<14.0)", **changes}
    with pytest.raises(ValueError, match=reason):
        builder.verify_wheel(wheel(builder.VERSION, **options), wheel("0.9.0"), source_files())


def test_source_and_published_runtime_must_agree():
    files = source_files()
    files["fastembed/__init__.py"] = b"different sdist"
    with pytest.raises(ValueError, match="Runtime files differ"):
        builder.verify_wheel(wheel(builder.VERSION), wheel("0.9.0"), files)


def test_extra_executable_wheel_member_is_rejected():
    data = io.BytesIO(wheel(builder.VERSION, dependency="pillow (>=12,<14.0)"))
    with zipfile.ZipFile(data, mode="a") as output:
        output.writestr("injected.pth", "import injected\n")
    with pytest.raises(ValueError, match="wheel member set"):
        builder.verify_wheel(data.getvalue(), wheel("0.9.0"), source_files())


def test_requirements_encode_paths_with_spaces(tmp_path):
    requirements = tmp_path / "app checkout" / "requirements.txt"
    artifact = tmp_path / "built wheels" / "fastembed.whl"
    text = builder.requirements_text(requirements, artifact, "abc123")
    assert f"-r {requirements.as_uri()}\n" in text
    assert f"fastembed @ {artifact.as_uri()}#sha256=abc123\n" in text
    assert "app%20checkout" in text
    assert "built%20wheels" in text
