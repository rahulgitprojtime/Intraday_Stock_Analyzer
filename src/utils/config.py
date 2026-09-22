"""Config loading. Single place that reads config/*.yaml so every layer
gets identical, validated config rather than re-parsing YAML ad hoc."""

from __future__ import annotations

from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def load_yaml(name: str) -> dict:
    """Load a YAML file from the config/ directory by filename (e.g.
    'settings.yaml')."""
    path = CONFIG_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings() -> dict:
    return load_yaml("settings.yaml")


def load_strategy() -> dict:
    strategy = load_yaml("strategy.yaml")
    weights = strategy.get("scoring", {}).get("weights", {})
    total = sum(weights.values())
    if weights and total != 100:
        raise ValueError(
            f"strategy.yaml scoring weights must sum to 100, got {total} ({weights})"
        )
    return strategy


def load_universe() -> dict:
    return load_yaml("universe.yaml")
