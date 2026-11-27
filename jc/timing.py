"""Objective timing defaults for the supported Summoner's Rift HUD."""
import json
from pathlib import Path

from .paths import STATIC

with (Path(STATIC) / "patch_config.json").open(encoding="utf-8") as config:
    OBJECTIVES = json.load(config)
