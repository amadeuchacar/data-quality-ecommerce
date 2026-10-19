"""
Shared project configuration: resolves the project root, the environment settings
(config/settings.yaml) and the metadata catalog (catalog/inventory.yaml).

Every pipeline stage imports from here, so paths and environment resolution live in
a single place.
"""

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_settings(path: Path = PROJECT_ROOT / "config" / "settings.yaml") -> dict[str, Any]:
    """Loads the settings file and returns the block of the active environment."""
    with open(path, encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    env = settings["environment"]
    resolved = settings[env]
    resolved["catalog_path"] = settings["catalog_path"]
    resolved["environment"] = env

    return resolved


def load_catalog(catalog_path: Path) -> dict[str, Any]:
    """Loads the metadata catalog (expects an absolute path or one relative to the cwd)."""
    with open(catalog_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
