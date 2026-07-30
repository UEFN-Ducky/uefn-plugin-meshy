# Meshy setup

1. Store → Install **Meshy** → Enable.
2. Settings → **Meshy** → paste API key (`msy_…` from https://www.meshy.ai/settings/api) → **Test** (calls balance).
3. Opt in `meshy_*` tools for the chat.
4. Optional Blender: install/enable **Blender** Store plugin once (deploys addon), open Blender, Connect on port `9876`.
5. Optional UEFN: Fortnite listener online for `meshy_import_to_uefn`.

Key is stored encrypted as `meshy_api_key` on this device.

Do **not** run `npx skills add meshy-dev/meshy-3d-agent` — this plugin already ships the `meshy-api` skill pack and `meshy_*` MCP tools.
