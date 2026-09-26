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


def validate_engine_weights(weights: dict) -> None:
    """M6 blend weights: required, positive, summing to 1.0 (DECISIONS #14)."""
    if not weights:
        raise ValueError("strategy.yaml engine.weights is required")
    bad = {k: v for k, v in weights.items() if not isinstance(v, (int, float)) or v <= 0}
    if bad:
        raise ValueError(f"engine weights must be positive numbers: {bad}")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"engine weights must sum to 1.0, got {weights}")


def load_strategy() -> dict:
    strategy = load_yaml("strategy.yaml")
    validate_engine_weights(strategy.get("engine", {}).get("weights", {}))
    return strategy


def load_universe() -> dict:
    return load_yaml("universe.yaml")
