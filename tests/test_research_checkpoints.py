"""Frozen offline fixtures: actual research loop, handler timeout and persistence.

The model, search and fetch are deterministic fakes. These prove orchestration
and prompt-boundary invariants, not real model quality or the operator gate.
"""
import asyncio
import json
import socket
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.deep_research import DeepResearcher
from src import research_handler as handler_module
from src.prompt_security import GUARD_CLOSE, GUARD_OPEN

CASES = json.loads((Path(__file__).parent / "fixtures" /
                   "research_checkpoint_cases.json").read_text(encoding="utf-8"))


async def run_case(monkeypatch, tmp_path, case, *, interrupt=None, prior=False):
    def no_network(*args, **kwargs):
        pytest.fail("Fixture attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(handler_module, "RESEARCH_DATA_DIR", tmp_path)
    monkeypatch.setattr(handler_module.ResearchHandler, "_initialize_legacy_engine", lambda self: None)
    monkeypatch.setattr(handler_module.ResearchHandler, "_probe_endpoint", AsyncMock())
    monkeypatch.setattr("src.event_bus.fire_event", lambda *args: None)
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: default)
    calls = {"queries": [], "fetches": [], "model": []}
    stop_at = interrupt or case.get("interrupt")

    async def plan(self, question):
        if stop_at == "plan":
            await asyncio.Event().wait()
        return "Use only the fictional fixture evidence."

    async def queries(self, question, report, round_num):
        if round_num > 1:
            if stop_at == "next-query":
                await asyncio.Event().wait()
            return []
        return ["fictional Atlas study"]

    async def search(self, query):
        calls["queries"].append(query)
        return case["sources"]

    def fetch(url, timeout):
        calls["fetches"].append(url)
        source = next(s for s in case["sources"] if s["url"] == url)
        return {"success": True, "content": source.get("source_text", source["summary"])}

    async def llm(self, messages, **kwargs):
        calls["model"].append({"messages": messages, "settings": kwargs})
        if len(messages) == 2:
            content = messages[1]["content"]
            source = next(s for s in case["sources"]
                          if s.get("source_text", s["summary"]) in content
                          or "<<<_END_UNTRUSTED_DATA>>>" in content)
            return json.dumps({"summary": source["summary"]})
        if stop_at == "first-synthesis":
            await asyncio.Event().wait()
        return case["report"]

    monkeypatch.setattr(DeepResearcher, "_create_plan", plan)
    monkeypatch.setattr(DeepResearcher, "_generate_queries", queries)
    monkeypatch.setattr(DeepResearcher, "_search", search)
    monkeypatch.setattr(DeepResearcher, "_llm", llm)
    monkeypatch.setattr(DeepResearcher, "_should_stop", AsyncMock(return_value=False))
    monkeypatch.setattr("src.search.fetch_webpage_content", fetch)
    handler = handler_module.ResearchHandler()
    completed = []
    started = time.perf_counter()
    handler.start_research(
        "fixture-run", case["question"], "http://fixture.invalid/v1", "frozen-model",
        hard_timeout=0.2 if stop_at else 10, max_rounds=2,
        category="factcheck", extraction_concurrency=1, owner="fixture-owner",
        on_complete=lambda *args: completed.append(args),
        prior_report=case["report"] if prior else "",
        prior_findings=case["sources"] if prior else None,
    )
    entry = handler._active_tasks["fixture-run"]
    await asyncio.wait_for(entry["task"], timeout=5)
    saved_path = tmp_path / "fixture-run.json"
    saved = json.loads(saved_path.read_text()) if saved_path.exists() else None
    calls["elapsed_seconds"] = time.perf_counter() - started
    return entry, saved, completed, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_frozen_research_cases(monkeypatch, tmp_path, case, record_property):
    entry, saved, completed, calls = await run_case(monkeypatch, tmp_path, case)
    record_property("metrics", json.dumps({
        "saved_sources": len(saved["sources"]) if saved else 0,
        "search_calls": len(calls["queries"]), "fetch_calls": len(calls["fetches"]),
        "model_calls": len(calls["model"]),
        "prompt_chars": sum(len(m["content"]) for c in calls["model"] for m in c["messages"]),
        "elapsed_seconds": calls["elapsed_seconds"], "status": entry["status"],
    }))
    assert entry["status"] == "done"
    assert saved is not None
    assert saved["owner"] == "fixture-owner"
    assert len(completed) == 1
    assert len(saved["sources"]) == len(case["sources"])
    if case["sources"]:
        assert case["report"] in saved["result"]
        for source in case["sources"]:
            assert source["url"] in saved["result"]
    else:
        assert "No information could be gathered" in saved["result"]
    if case.get("interrupt"):
        assert "partial" in saved["result"].lower()
        assert saved["raw_report"]
        assert saved["stats"]["Model"] == "frozen-model"
    outbound = json.dumps(calls["queries"] + calls["fetches"])
    assert "SYNTHETIC_PRIVATE_SENTINEL_7F31" not in outbound
    assert "/collect" not in outbound
    assert calls["queries"] == ["fictional Atlas study"]
    for call in calls["model"]:
        if len(call["messages"]) == 2:
            source_message = call["messages"][1]
            assert source_message["role"] == "user"
            assert source_message["metadata"]["trusted"] is False
            assert source_message["metadata"]["tool_gate_untrusted"] is True
            assert source_message["content"].count(GUARD_OPEN) == 1
            assert source_message["content"].count(GUARD_CLOSE) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt,prior", [("first-synthesis", False), ("plan", True)])
async def test_timeout_preserves_unsynthesized_or_prior_evidence(monkeypatch, tmp_path, interrupt, prior):
    case = CASES[0]
    entry, saved, completed, calls = await run_case(
        monkeypatch, tmp_path, case, interrupt=interrupt, prior=prior)
    assert entry["status"] == "done"
    assert saved is not None
    assert len(completed) == 1
    assert saved["owner"] == "fixture-owner"
    assert "partial" in saved["result"].lower()
    assert case["sources"][0]["url"] in saved["result"]
    assert len(saved["sources"]) == 1
    assert len(saved["raw_findings"]) == 1
    assert saved["raw_report"]
    assert saved["stats"]["Model"] == "frozen-model"


@pytest.mark.asyncio
async def test_timeout_without_evidence_stays_error(monkeypatch, tmp_path):
    entry, saved, completed, calls = await run_case(
        monkeypatch, tmp_path, CASES[2], interrupt="plan")
    assert entry["status"] == "error"
    assert saved is None
    assert completed == []
    assert calls["queries"] == calls["fetches"] == calls["model"] == []
