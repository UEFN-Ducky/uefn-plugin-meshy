# Meshy examples

## Discover FREE community first (always)

```
meshy_discover_search(query="wooden treasure chest", limit=8)
# paste chat_links in chat — user previews on meshy.ai
# if they pick one: they download FREE GLB/FBX in browser → local path:
# FIRST: get_project_info() → content_root (e.g. /MyProject/)
meshy_import_to_uefn(url_or_path="C:/Users/.../chest.glb", destination_path="/MyProject/Meshy/Props")
# or omit destination_path / pass "" → relative "Meshy" (listener pins)
# only if they reject free matches:
meshy_balance()
ducky_ask_user(questions=[{
  "id": "spend",
  "prompt": "Spend ~25 Meshy credits to generate a stylized wooden treasure chest?",
  "options": [{"id": "yes", "label": "Spend ~25 credits"}, {"id": "no", "label": "Cancel"}],
  "required": true
}])
# only after they pick yes:
meshy_text_to_3d(prompt="stylized wooden treasure chest, game prop", confirm_spend=true, wait=true)
```

## Text → textured GLB → UEFN

```
meshy_discover_search(query="stylized wooden treasure chest")
# …user rejects free matches…
meshy_balance()
ducky_ask_user(… spend ~25 credits …)  # required every paid job
meshy_text_to_3d(prompt="stylized wooden treasure chest, game prop", confirm_spend=true, wait=true)
meshy_import_to_uefn(url_or_path="<glb url>", destination_path="/MyProject/Meshy/Props")
```

## Text → Blender cleanup

```
# after ducky_ask_user OK for the generate cost:
meshy_text_to_3d(prompt="low poly lantern", confirm_spend=true, wait=true)
meshy_import_to_blender(url_or_path="<glb url>")
# then blender skill: cleanup → uefn_export → import_asset
```

## Text → image → 3D (controlled concept)

```
# ducky_ask_user once if you named both steps up front, else ask per paid tool
meshy_text_to_image(prompt="front view of a cartoon robot, white background", confirm_spend=true, wait=true)
meshy_image_to_3d(input_task_id="<image task id>", confirm_spend=true, wait=true)
meshy_import_to_blender(url_or_path="<glb url>")
```

## Character → rig → walk → Blender

```
# Every line below is a PAID job: meshy_balance() → ducky_ask_user naming the whole
# chain and its ~credit estimate → only then confirm_spend=true on each call.
meshy_text_to_image(prompt="stylized knight A-pose", pose_mode="a-pose", generate_multi_view=true, confirm_spend=true, wait=true)
meshy_image_to_3d(input_task_id="<img id>", confirm_spend=true, wait=true)
# if face_count > 300000:
meshy_remesh(input_task_id="<3d id>", target_polycount=100000, confirm_spend=true, wait=true)
meshy_rig(input_task_id="<textured or remesh id>", height_meters=1.8, confirm_spend=true, wait=true)
# result.basic_animations.walking_fbx_url / running_fbx_url
meshy_import_to_blender(url_or_path="<walking_fbx_url>")
# blender skill skeletal_export → UEFN
```

## Custom animation from library

```
meshy_list_animations(query="punch", limit=20)          # free
# ducky_ask_user for the animate spend first
meshy_animate(rig_task_id="<rig id>", action_id=92, confirm_spend=true, wait=true)
meshy_download(task_id="<anim id>", kind="animations")
```

## Convert to FBX for engine

```
meshy_convert(input_task_id="<3d id>", target_formats="fbx", confirm_spend=true, wait=true)   # paid → ask first
meshy_import_to_uefn(url_or_path="<fbx url>", destination_path="/MyProject/Meshy")
```

## Local photo → 3D

```
# paid: meshy_balance() → ducky_ask_user → then:
meshy_image_to_3d(image="C:/Users/me/refs/prop.png", confirm_spend=true, wait=true)
```

## Preview then refine separately

```
meshy_text_to_3d_preview(prompt="dragon egg", confirm_spend=true, wait=true)   # paid → ask first
meshy_text_to_3d_refine(preview_task_id="<preview id>", enable_pbr=true, confirm_spend=true, wait=true)   # paid → ask first
```
