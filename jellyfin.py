"""Jellyfin backgrounds — overlay style.

Run directly:
    python jellyfin.py
"""
import os

import yaml
from dotenv import load_dotenv

from androidtvbackground.common import (
    SharedConfig,
    JellyfinConfig,
    JellyfinGenerator,
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

    cfg = JellyfinConfig.from_env()
    # Optional overrides:
    # cfg.order_by = "mix"         # 'added', 'aired', or 'mix'
    # cfg.download_movies = True
    # cfg.download_series = True
    # cfg.limit = 10
    # cfg.excluded_genres = ['Horror', 'Thriller']
    # cfg.excluded_tags = ['Adult']
    # cfg.excluded_libraries = ['Web Videos']

    yaml_cfg = _load_yaml(os.path.join(source_dir, "config/config.yaml"))
    _apply(shared, yaml_cfg, "shared")
    _apply(cfg, yaml_cfg, "jellyfin")

    JellyfinGenerator(cfg, shared, make_renderer(shared), make_font_manager(shared), source_dir).run()
