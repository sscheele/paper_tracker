"""Configuration loading."""

from pathlib import Path

import yaml

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "paper-downloader"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"
DEFAULT_DB_PATH = DEFAULT_CONFIG_DIR / "papers.db"


def load_config(path: Path | None = None, create_if_missing: bool = False) -> dict:
    """Load YAML config from path, falling back to default location.

    If create_if_missing is True, creates a minimal config file when none exists.
    """
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        if create_if_missing:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            default = {
                "mailto": "",
                "authors": [],
                "lookback_days": 7,
                "max_results_per_author": 50,
            }
            with open(config_path, "w") as f:
                yaml.dump(default, f, default_flow_style=False, sort_keys=False)
            return default
        raise FileNotFoundError(
            f"Config not found at {config_path}. "
            f"Copy config.example.yaml to {DEFAULT_CONFIG_PATH} and edit it."
        )
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    config.setdefault("mailto", "")
    config.setdefault("authors", [])
    config.setdefault("lookback_days", 7)
    config.setdefault("max_results_per_author", 50)
    return config


def save_config(config: dict, path: Path | None = None):
    """Write config dict back to YAML file."""
    config_path = path or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
