"""Meshy — Store desktop plugin (API tools on shared uefn-ducky MCP)."""

from __future__ import annotations

import logging

from . import meshy

log = logging.getLogger("uefn.plugin.meshy")
PLUGIN_ID = "meshy"


def register(api) -> None:
    meshy.register_tools(api)
    api.log("meshy tools registered")
