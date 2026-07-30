---
name: meshy-api
description: "Meshy AI via UEFN-Ducky MCP — Discover FREE community models first, then text/image-to-3D, remesh/retexture/convert/resize/UV, auto-rig, animate, download, import to Blender or UEFN. Async: submit → task_id → poll → download/import."
license: All Rights Reserved
metadata:
  label: Meshy
  version: 7
  author: UEFN-Ducky
  copyright: Copyright 2026 UEFN-Ducky
  allow_redistribute: false
  managed_by: uefn-ducky
  source_plugin_id: meshy
---

# Meshy — generate via MCP

You call **`meshy_*` tools** on the shared `uefn-ducky` MCP. This Store plugin **is** the Ducky equivalent of Meshy's public `meshy-dev/meshy-3d-agent` skill pack.

**Do not** run `npx skills add meshy-dev/meshy-3d-agent`, install `@meshy-ai/meshy-mcp-server`, shell/curl the REST API, or read a `.env` / `MESHY_API_KEY`. The key lives in **Settings → Meshy** (encrypted; use **Test**). Independent of Blender and of **studio3d**.

## Prerequisites

1. Plugin **meshy** installed + enabled (Settings → Store).
2. Tools opted in for this chat.
3. Meshy API key pasted + Tested (`msy_…`, Pro+) — **not required** for Discover browse.
4. Blender import: Blender + BlenderMCP on `localhost:9876`.
5. UEFN import: Fortnite listener online.

## HARD RULES — DISCOVER FIRST (FREE ONLY)

Before any paid `meshy_*` generate / remesh / retexture / rig / animate:

1. Call `meshy_discover_search(query=…)` for the thing the user wants.
2. Paste `chat_links` (and thumbnails from `meshy_discover_get` when useful) **in chat** so the user can look.
3. Prefer a free community match over spending credits. Ask: use this free model, or generate new?
4. **FREE downloads only** — never spend generation credits to “get” a community model.
   - Meshy OpenAPI does **not** expose community GLB. User downloads GLB/FBX on the page while logged in (community download quota ≠ generation credits).
   - Then `meshy_discover_download(local_path)` or `meshy_import_to_blender` / `meshy_import_to_uefn` with that path.
5. Only after the user rejects free matches (or search is empty) may you propose a paid generate — then **always** `ducky_ask_user` before `confirm_spend=true`.

## Core pattern (all jobs are async)

1. **Discover free first** (above).
2. Submit → JSON with `task_id` + `kind`
3. `meshy_status(task_id, kind)` or `poll=true` / `wait=true` until `SUCCEEDED` / `FAILED`
4. `meshy_download(task_id, kind)` and/or land with `meshy_import_to_blender` / `meshy_import_to_uefn`

**Text-to-3D is two steps:** preview → refine. Prefer `meshy_text_to_3d` for the full pipeline.

Assets expire in ~3 days — download promptly. Check `meshy_balance` before expensive chains.

## Tools

| Tool | When |
|------|------|
| `meshy_discover_search` | **First** — FREE community / Discover browse; paste `chat_links` |
| `meshy_discover_get` | One community page (title, thumbnail, `page_url`) |
| `meshy_discover_download` | FREE-only local/direct `.glb`/`.fbx` (not proprietary `.meshy`) |
| `meshy_balance` | Credit check |
| `meshy_status` / `meshy_download` | Poll / save — **kind required** |
| `meshy_text_to_3d` (+ preview/refine) | Text→3D |
| `meshy_image_to_3d` / `meshy_multi_image_to_3d` | Image→3D |
| `meshy_text_to_image` / `meshy_image_to_image` | Concept art → feed image→3D |
| `meshy_retexture` / `meshy_remesh` | Style / polycount |
| `meshy_convert` | Formats (fbx, glb, …) |
| `meshy_resize` | Real-world meters |
| `meshy_uv_unwrap` | Clean UVs before external texture |
| `meshy_rig` | Auto-rig humanoid (≤300k faces; remesh first if needed) |
| `meshy_list_animations` | Search library → `action_id` |
| `meshy_animate` | Apply animation to a `rig_task_id` |
| `meshy_import_to_blender` | GLB/GLTF/FBX → Blender (`meshy_import_glb_to_blender` alias) |
| `meshy_import_to_uefn` | File/URL → Content Browser via `import_asset` |

Kinds for status/download: `text-to-3d`, `image-to-3d`, `multi-image-to-3d`, `text-to-image`, `image-to-image`, `remesh`, `retexture`, `convert`, `resize`, `uv-unwrap`, `rigging`, `animations`.

## Pipelines

### Static prop → UEFN

1. `meshy_discover_search` → show links; if user picks one → browser free download → import local path
2. Else: `meshy_balance` → `ducky_ask_user` (spend yes/no + estimate) → only on approve: `meshy_text_to_3d` / `meshy_image_to_3d` (`confirm_spend=true`, `wait=true`)
3. Optional: `meshy_convert(target_formats="fbx")` if you need FBX
4. `meshy_import_to_uefn(url_or_path=<glb/fbx>, destination_path="/VideoTest/Meshy/Props")` — first `get_project_info()` for `content_root`; or omit / pass `""` (defaults to relative `Meshy`, listener pins)
5. Blender only if cleanup needed → **blender** skill `uefn_export` → `import_asset`

### Character → rig → animate → Blender → UEFN

1. Prefer A/T-pose concept: `meshy_text_to_image(..., pose_mode="a-pose", generate_multi_view=true)` → `meshy_image_to_3d`
2. If `face_count` > 300000 → `meshy_remesh(target_polycount=100000)` first
3. `meshy_rig(input_task_id=…, height_meters=1.7, wait=true)` — includes walk/run in `result.basic_animations`
4. Custom move: `meshy_list_animations(query="run")` → `meshy_animate(rig_task_id=…, action_id=…, wait=true)`
5. `meshy_download` or import FBX/GLB → `meshy_import_to_blender`
6. Cleanup / combine in Blender → **blender** skill `skeletal_export` (not invent UEFN skeletal import here) → `import_asset`

### Combine multiple Meshy assets

Download each → `meshy_import_to_blender` for each → Blender join/parent/export → UEFN.

## vs 3D AI Studio (`studio3d`)

Same pattern (MCP + Settings key + Blender/UEFN). Different vendor/credits. Use whichever key the user has.

## HARD RULES — CREDITS (non-negotiable)

Paid `meshy_*` create tools **refuse** unless `confirm_spend=true`. Free: discover_*, balance, status, download, list_animations, imports.

**100% of paid spends:** call `ducky_ask_user` first. Never ask only in chat text. Never set `confirm_spend=true` without a modal approve on that spend.

1. **Discover FREE community first** (see above) — do not skip.
2. Call `meshy_balance` before proposing paid work.
3. Call **`ducky_ask_user`** with a required yes/no question that includes the **~credit estimate** and what job will run. Example options: Spend / Cancel. Do not ask spend approval in plain chat.
4. Only if they select Spend (or clear free-text yes): retry the same tool with `confirm_spend=true`.
5. Never invent approval. Silence, skipped_all, Cancel, or vague interest = do **not** spend.
6. Never chain extra paid jobs without a new `ducky_ask_user` OK.
7. One modal OK covers one stated job (or a short list you named in that question) — not unlimited follow-ups.

## Don'ts

- Don't invent task status — poll with the correct `kind`.
- Don't tell the user to install Meshy npm MCP / `npx skills add meshy-dev/meshy-3d-agent`.
- Don't skip Discover when the request is “a sword / chest / character like X”.
- Don't spend credits to recreate a free community model the user would accept.
- Don't skip refine after preview if they want textures.
- Don't rig non-humanoids or huge meshes without remesh.
- Don't invent skeletal UEFN import — hand off to **blender** `skeletal_export` / `uefn_export`.
- Don't ask spend approval only in chat — always `ducky_ask_user`.
- Don't call paid tools with `confirm_spend=true` unless `ducky_ask_user` just approved that spend.

## Reference files

Load with MCP `skill_read_subskill("meshy-api", "<id>")`. Do **not** IDE-Read `~/.claude/skills` / `references/*.md` outside the workspace.

- `api_reference` [plugin]
- `examples` [plugin]
- `setup` [plugin]
