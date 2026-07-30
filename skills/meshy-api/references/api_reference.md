# Meshy API notes (via our tools)

Base: `https://api.meshy.ai/openapi/` — Bearer `msy_…`.

## Task kinds (for `meshy_status` / `meshy_download`)

| kind | Create |
|------|--------|
| `text-to-3d` | preview / refine / `meshy_text_to_3d` |
| `image-to-3d` | `meshy_image_to_3d` |
| `multi-image-to-3d` | `meshy_multi_image_to_3d` |
| `text-to-image` | `meshy_text_to_image` |
| `image-to-image` | `meshy_image_to_image` |
| `retexture` | `meshy_retexture` |
| `remesh` | `meshy_remesh` |
| `convert` | `meshy_convert` |
| `resize` | `meshy_resize` |
| `uv-unwrap` | `meshy_uv_unwrap` |
| `rigging` | `meshy_rig` |
| `animations` | `meshy_animate` |

Statuses: `PENDING` → `IN_PROGRESS` → `SUCCEEDED` | `FAILED` | `CANCELED`.

Rigging/animation tasks put URLs under nested `result` (also flattened into `results[]`). Downloads prefer **FBX then GLB** for those kinds.

## Text to 3D

Two-stage: `mode=preview` then `mode=refine` with `preview_task_id`.  
`ai_model`: `latest` / `meshy-6` / `meshy-5`.

## Image to 3D

`image_url` (https or data URI) **or** `input_task_id` from a succeeded text/image-to-image task.

## Mesh ops

- **Convert** `target_formats`: `glb`, `fbx`, `obj`, `usdz`, `blend`, `stl`, `3mf`
- **Resize**: exactly one of `resize_height`, `resize_longest_side`, `auto_size`
- **UV unwrap**: GLB-oriented; keep face count modest
- **Remesh**: use before rig if faces > 300k

## Rigging / animation

- Rig: textured **humanoid**, face toward +Z for `model_url`, ≤300k faces
- Rig result includes `basic_animations` walk/run
- Custom: `meshy_list_animations` → `action_id` → `meshy_animate(rig_task_id, action_id)`
- Optional `post_process_op`: `change_fps` | `fbx2usdz` | `extract_armature`

## Image models (`meshy_text_to_image`)

`nano-banana` (cheaper), `nano-banana-2`, `nano-banana-pro`, `gpt-image-2`.  
Characters: `pose_mode` `a-pose` / `t-pose`, `generate_multi_view=true`.

## Credits

Check `meshy_balance` first. Pricing: https://docs.meshy.ai/api/pricing.

## Landing

- `meshy_import_to_blender` — BlenderMCP `localhost:9876` (GLB/GLTF/FBX)
- `meshy_import_to_uefn` — listener `import_asset` (static props); skeletal path via Blender skill
