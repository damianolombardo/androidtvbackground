"""Plex friend-library backgrounds — overlay style.

Run directly:
    python plexfriend.py
"""
import os

import yaml
from dotenv import load_dotenv

from androidtvbackground.common import (
    SharedConfig,
    PlexFriendConfig,
    PlexFriendGenerator,
    make_font_manager,
    make_renderer,
    setup_logging,
)


def _load_yaml(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


def _apply(obj, yaml_cfg, section):
    for k, v in yaml_cfg.get(section, {}).items():
        if hasattr(obj, k):
            setattr(obj, k, tuple(v) if isinstance(v, list) else v)


load_dotenv(verbose=True)
setup_logging()

if __name__ == "__main__":
    source_dir = os.path.dirname(os.path.abspath(__file__))

    shared = SharedConfig.from_env()
    # Default to "overlay" but honour BACKGROUND_STYLE env var if set
    if not os.getenv("BACKGROUND_STYLE"):
        shared.background_style = "overlay"

    cfg = PlexFriendConfig.from_env()
    # Optional overrides:
    # cfg.target_friend = "Alice Dupont"  # None = all shared friends
    # cfg.order_by = "added"    # 'aired', 'added', or 'mix'
    # cfg.download_movies = True
    # cfg.download_series = True
    # cfg.limit = 5

    yaml_cfg = _load_yaml(os.path.join(source_dir, "config/config.yaml"))
    _apply(shared, yaml_cfg, "shared")
    _apply(cfg, yaml_cfg, "plexfriend")

    PlexFriendGenerator(cfg, shared, make_renderer(shared), make_font_manager(shared), source_dir).run()
