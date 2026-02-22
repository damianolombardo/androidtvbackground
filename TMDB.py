"""TMDB trending backgrounds — overlay style.

Run directly:
    python TMDB.py
"""
import os

import yaml
from dotenv import load_dotenv

from androidtvbackground.common import (
    SharedConfig,
    TMDBConfig,
    TMDBGenerator,
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

    cfg = TMDBConfig.from_env()
    # Optional overrides (not available as env vars):
    # cfg.tv_excluded_countries = ['jp', 'kr']
    # cfg.tv_excluded_genres = {'jp': ['Animation'], 'kr': ['Animation', 'Drama']}
    # cfg.movie_excluded_countries = []
    # cfg.movie_excluded_genres = {}
    # cfg.excluded_keywords = []

    yaml_cfg = _load_yaml(os.path.join(source_dir, "config/config.yaml"))
    _apply(shared, yaml_cfg, "shared")
    _apply(cfg, yaml_cfg, "tmdb")

    TMDBGenerator(cfg, shared, make_renderer(shared), make_font_manager(shared), source_dir).run()
