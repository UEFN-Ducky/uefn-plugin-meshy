"""The Meshy: Character bundle in plugin.json: shape, wiring and node types (no live calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPOS = Path(__file__).resolve().parent.parent.parent


def _row(plugin: str, rid: str):
    data = json.loads((_REPOS / plugin / "plugin.json").read_text(encoding="utf-8"))
    return next(r for r in data["contributes"]["automations"]["templates"] if r["id"] == rid)


ROW = _row("uefn-plugin-meshy", "meshy-character")
WORKFLOWS = {w["key"]: w for w in ROW["bundle"]["workflows"]}

# Core nodes the bundle uses (mesh.* / image.* / llm.* / uefn.* ship with the app).
NODE_TYPES = {
    "start.chat", "flow.input", "flow.output", "flow.branch", "flow.end", "flow.repeat", "flow.choose", "workflow.call",
    "notify.message", "pipeline.finish", "tool.call", "text.template", "logic.expression", "logic.if",
    "input.text", "list.make", "llm.extract", "llm.ask", "llm.vision", "image.generate", "image.edit", "image.remove_bg",
    "mesh.multi_view", "mesh.remesh", "mesh.auto_scale", "mesh.render", "mesh.rig", "mesh.animate",
    "uefn.open_project", "uefn.import",
}
KEYS = {"character", "spec", "sheet", "make3d", "rig", "gear", "ship"}
SHARED = ("spec", "sheet", "ship")
CALLS = {"sp": "spec", "sh": "sheet", "m3": "make3d", "rg": "rig", "gr": "gear", "sp2": "ship"}


def test_bundle_shape():
    assert set(WORKFLOWS) == KEYS
    assert len(ROW["bundle"]["workflows"]) == 7
    assert ROW["bundle"]["workflows"][0]["key"] == "character"  # main = first top-level workflow
    assert "meshy" in ROW["requires_plugins"]


def test_nodes_and_edges_are_wired():
    for key, wf in WORKFLOWS.items():
        nodes = {n["id"]: n for n in wf["graph"]["nodes"]}
        assert len(nodes) == len(wf["graph"]["nodes"]), key
        for n in nodes.values():
            assert n["type"] in NODE_TYPES, (key, n["type"])
        for e in wf["graph"]["edges"]:
            assert e["source"] in nodes and e["target"] in nodes, (key, e)
            if e["kind"] == "data":
                assert e["source_pin"] and e["target_pin"], (key, e)


def test_no_remove_background():
    types = {n["type"] for wf in WORKFLOWS.values() for n in wf["graph"]["nodes"]}
    assert "image.remove_bg" not in types


def test_calls_point_at_bundle_keys():
    calls = [n["config"]["workflow_id"] for n in WORKFLOWS["character"]["graph"]["nodes"] if n["type"] == "workflow.call"]
    assert sorted(calls) == sorted(f"@{k}" for k in KEYS - {"character"})


def test_call_wires_match_callee_signature():
    def pins(key, ntype, field):
        return {r["name"] for n in WORKFLOWS[key]["graph"]["nodes"] if n["type"] == ntype for r in n["config"][field]}

    for e in WORKFLOWS["character"]["graph"]["edges"]:
        if e["kind"] != "data":
            continue
        if e["target"] in CALLS:
            assert e["target_pin"] in pins(CALLS[e["target"]], "flow.input", "inputs"), e
        if e["source"] in CALLS:
            assert e["source_pin"] in pins(CALLS[e["source"]], "flow.output", "outputs"), e
    # Tripo's signatures: Spec(prompt) -> spec, prompts, run_title; Sheet(spec, prompts) -> views; Ship(spec, meshes)
    assert pins("spec", "flow.input", "inputs") == {"prompt"}
    assert pins("spec", "flow.output", "outputs") == {"spec", "prompts", "run_title"}
    assert pins("sheet", "flow.input", "inputs") == {"spec", "prompts"}
    assert pins("sheet", "flow.output", "outputs") == {"views"}
    assert pins("ship", "flow.input", "inputs") == {"spec", "meshes"}
    assert pins("ship", "flow.output", "outputs") == {"assets", "mesh_path"}


def test_quadruped_stops_before_paid_steps():
    main = WORKFLOWS["character"]["graph"]
    edges = main["edges"]
    nodes = {n["id"]: n for n in main["nodes"]}
    assert nodes["bq"]["config"]["field"] == "spec.body" and nodes["bq"]["config"]["equals"] == "quadruped"
    assert {"source": "bq", "target": "nq", "kind": "true"} in edges
    assert {"source": "nq", "target": "eq", "kind": "main"} in edges
    assert "Tripo: Character" in nodes["nq"]["config"]["inputs"]["message"]
    # only the false edge reaches the concept sheet (the first paid step)
    assert [e["target"] for e in edges if e["source"] == "bq" and e["kind"] == "false"] == ["sh"]


def test_nothing_paid_before_approval():
    # Concept sheet returns only when approved (otherwise the path stops), so Make 3D / Rig sit behind it.
    edges = WORKFLOWS["character"]["graph"]["edges"]
    mains = {(e["source"], e["target"]) for e in edges if e["kind"] == "main"}
    assert ("sh", "m3") in mains
    assert [e for e in WORKFLOWS["sheet"]["graph"]["edges"] if e["kind"] == "true"] == [{"source": "gate", "target": "ret", "kind": "true"}]
    for paid in ("m3", "rg"):
        assert all(src in ("sh", "mb") for src, dst in mains if dst == paid)


def test_shared_workflows_match_tripo():
    tripo = {w["key"]: w for w in _row("uefn-plugin-tripo", "tripo-character")["bundle"]["workflows"]}
    for key in SHARED:
        assert WORKFLOWS[key] == tripo[key], key


def test_bundle_validates_in_the_app():
    try:
        from backend.automations.templates import bundle_of
    except ImportError:  # plugin repo alone: the app's own check isn't here
        pytest.skip("app not importable")
    bundle = bundle_of(ROW)
    assert bundle and len(bundle["workflows"]) == len(KEYS)
