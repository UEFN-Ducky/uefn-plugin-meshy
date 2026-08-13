# Meshy

Meshy AI API — Discover free community models first, then text/image-to-3D, remesh/retexture/convert/resize, auto-rig, animate. Import into Blender or UEFN Content Browser.

Desktop plugin for [UEFN-Ducky](https://github.com/UEFN-Ducky/UEFN-Ducky) (`meshy`).
Install or update from **Settings → Store** in the app — do not install from a zip by hand.

## Build

```bash
py scripts/build_zip.py
```

Writes `deploy/meshy-1.0.12.ducky-plugin.zip` (scripts/ and deploy/ are not packed).

## Secrets

Never commit tokens or keys. The app stores `meshy_api_key` locally (DPAPI), not in this package.
