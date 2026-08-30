import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


def _resolve_rag(toggle_state):
    js = f"""
    globalThis.localStorage = {{
      getItem(key) {{
        if (key === 'odysseus-toggles') return {json.dumps(json.dumps(toggle_state))};
        return null;
      }},
      setItem() {{}},
    }};

    const Storage = (await import('./static/js/storage.js')).default;
    console.log(JSON.stringify(Storage.getToggle('rag', true)));
    """

    result = subprocess.run(
        ["node", "--input-type=module", "-e", js],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({}, True),
        ({"rag": False}, False),
        ({"rag": True}, True),
    ],
)
def test_document_rag_default_preserves_explicit_preference(state, expected):
    assert _resolve_rag(state) is expected


def test_app_initializes_rag_with_default_on_fallback():
    source = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "Storage.getToggle('rag', true)" in source
