"""Self-check for Meshy helpers (no live API calls)."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

try:
    from .meshy import (
        auth_header,
        blender_import_code,
        credit_gate,
        encode_image,
        extract_model_slugs,
        filter_animations,
        format_balance_detail,
        model_entry_from_slug,
        normalize_kind,
        parse_model_slug,
        parse_task_payload,
        resolve_image,
        slugify_discover_query,
        sort_download_assets,
        test_api_key as check_api_key,
    )
except ImportError:
    from meshy import (
        auth_header,
        blender_import_code,
        credit_gate,
        encode_image,
        extract_model_slugs,
        filter_animations,
        format_balance_detail,
        model_entry_from_slug,
        normalize_kind,
        parse_model_slug,
        parse_task_payload,
        resolve_image,
        slugify_discover_query,
        sort_download_assets,
        test_api_key as check_api_key,
    )


def test_auth_header_bearer() -> None:
    h = auth_header("msy_test_key")
    assert h["Authorization"] == "Bearer msy_test_key"
    assert h["Content-Type"] == "application/json"


def test_normalize_kind() -> None:
    assert normalize_kind("text_to_3d") == "text-to-3d"
    assert normalize_kind("i23d") == "image-to-3d"
    assert normalize_kind("t2i") == "text-to-image"
    assert normalize_kind("rig") == "rigging"
    assert normalize_kind("animate") == "animations"
    assert normalize_kind("uv_unwrap") == "uv-unwrap"


def test_parse_task_succeeded() -> None:
    parsed = parse_task_payload(
        {
            "id": "abc",
            "type": "image-to-3d",
            "status": "SUCCEEDED",
            "progress": 100,
            "model_urls": {
                "glb": "https://assets.meshy.ai/x/model.glb",
                "fbx": "https://assets.meshy.ai/x/model.fbx",
            },
            "task_error": {"message": ""},
        }
    )
    assert parsed["finished"] is True
    assert parsed["failed"] is False
    assert any(a.get("format") == "glb" for a in parsed["results"])


def test_parse_rig_nested_result() -> None:
    parsed = parse_task_payload(
        {
            "id": "rig1",
            "type": "rig",
            "status": "SUCCEEDED",
            "progress": 100,
            "result": {
                "rigged_character_glb_url": "https://assets.meshy.ai/r/char.glb",
                "rigged_character_fbx_url": "https://assets.meshy.ai/r/char.fbx",
                "basic_animations": {
                    "walking_fbx_url": "https://assets.meshy.ai/r/walk.fbx",
                    "walking_glb_url": "https://assets.meshy.ai/r/walk.glb",
                },
            },
        }
    )
    assert parsed["result"] is not None
    keys = {a.get("key") for a in parsed["results"]}
    assert "rigged_character_fbx_url" in keys
    assert "walking_fbx_url" in keys
    ordered = sort_download_assets(parsed["results"], kind="rigging")
    assert ordered[0]["format"] == "fbx"


def test_parse_animation_nested_result() -> None:
    parsed = parse_task_payload(
        {
            "status": "SUCCEEDED",
            "type": "animate",
            "result": {
                "animation_glb_url": "https://assets.meshy.ai/a/anim.glb",
                "animation_fbx_url": "https://assets.meshy.ai/a/anim.fbx",
            },
        }
    )
    ordered = sort_download_assets(parsed["results"], kind="animations")
    assert ordered[0]["format"] == "fbx"
    assert ordered[1]["format"] == "glb"


def test_parse_task_failed() -> None:
    parsed = parse_task_payload(
        {
            "status": "FAILED",
            "progress": 0,
            "task_error": {"message": "bad prompt"},
        }
    )
    assert parsed["failed"] is True
    assert parsed["failure_reason"] == "bad prompt"


def test_encode_and_resolve_image() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.png"
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        p.write_bytes(png)
        uri = encode_image(str(p))
        assert uri.startswith("data:image/png;base64,")
        assert resolve_image(str(p)).startswith("data:image/png;")
        assert resolve_image("https://example.com/a.png") == "https://example.com/a.png"
        assert resolve_image(uri) == uri


def test_format_balance_detail() -> None:
    assert format_balance_detail({"balance": 42}) == "Connected — 42 credits"
    assert format_balance_detail({}) == "Connected"


def test_api_key_empty() -> None:
    res = check_api_key("")
    assert res["ok"] is False
    assert "Paste" in res["detail"]


def test_filter_animations() -> None:
    rows = [
        {
            "action_id": 1,
            "name": "Walk Forward",
            "category": "Locomotion",
            "sub_category": "Walk",
            "key": "walk_fwd",
        },
        {
            "action_id": 2,
            "name": "Punch Combo",
            "category": "Combat",
            "sub_category": "Melee",
            "key": "punch",
        },
    ]
    hit = filter_animations(rows, query="punch", limit=10)
    assert len(hit) == 1 and hit[0]["action_id"] == 2
    hit2 = filter_animations(rows, category="locomotion", limit=10)
    assert len(hit2) == 1 and hit2[0]["action_id"] == 1


def test_blender_import_code() -> None:
    assert "import_scene.gltf" in blender_import_code(r"C:\tmp\a.glb")
    assert "import_scene.fbx" in blender_import_code(r"C:\tmp\a.fbx")


def test_credit_gate() -> None:
    msg = credit_gate(False, 25, "text_to_3d") or ""
    assert "CREDIT GATE" in msg
    assert "meshy_discover_search" in msg
    assert "ducky_ask_user" in msg
    assert credit_gate(True, 25, "text_to_3d") is None


def test_discover_slug_helpers() -> None:
    assert slugify_discover_query("Wooden Sword!!") == "wooden-sword"
    parsed = parse_model_slug("Clockwork-Falchion-019f1caa-bf81-7f17-868d-46db8366117c")
    assert parsed["id"] == "019f1caa-bf81-7f17-868d-46db8366117c"
    assert "Falchion" in parsed["title"]
    html = 'href="/3d-models/Foo-Bar-019f1caa-bf81-7f17-868d-46db8366117c" and again /3d-models/Foo-Bar-019f1caa-bf81-7f17-868d-46db8366117c'
    assert extract_model_slugs(html) == ["Foo-Bar-019f1caa-bf81-7f17-868d-46db8366117c"]
    entry = model_entry_from_slug("Foo-Bar-019f1caa-bf81-7f17-868d-46db8366117c")
    assert entry["free_community"] is True
    assert entry["page_url"].endswith("/3d-models/Foo-Bar-019f1caa-bf81-7f17-868d-46db8366117c")


if __name__ == "__main__":
    test_auth_header_bearer()
    test_normalize_kind()
    test_parse_task_succeeded()
    test_parse_rig_nested_result()
    test_parse_animation_nested_result()
    test_parse_task_failed()
    test_encode_and_resolve_image()
    test_format_balance_detail()
    test_api_key_empty()
    test_filter_animations()
    test_blender_import_code()
    test_credit_gate()
    test_discover_slug_helpers()
    print("meshy self-check ok")
