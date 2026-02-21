#!/usr/bin/env python3
"""Android TV Background Generator — entry point.

Run all configured services automatically:

    python main.py

Force a specific generator regardless of configuration:

    python main.py --gen tmdb
    python main.py --gen jellyfin
    python main.py --gen plex
    python main.py --gen plexfriend
    python main.py --gen radarrsonarr
    python main.py --gen trakt
    python main.py --gen lidarr
    python main.py --gen steam

Service detection relies on environment variables set in .env.
Behavioural settings (limits, labels, filters, colours) come from config.yaml.
"""

from __future__ import annotations

import argparse
import os
import sys

import yaml
from dotenv import load_dotenv

from common import (
    JellyfinConfig,
    JellyfinGenerator,
    LidarrConfig,
    LidarrGenerator,
    PlexConfig,
    PlexFriendConfig,
    PlexFriendGenerator,
    PlexGenerator,
    RadarrSonarrConfig,
    RadarrSonarrGenerator,
    SharedConfig,
    SteamConfig,
    SteamGenerator,
    TMDBConfig,
    TMDBGenerator,
    TraktConfig,
    TraktGenerator,
    acquire_runlock,
    make_font_manager,
    make_renderer,
    setup_logging,
    validate_source_files,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: str) -> dict:
    """Load *path* as YAML; return an empty dict when the file is absent or invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as exc:
        print(
            f"WARNING: YAML parse error in {path!r}: {exc} — using defaults",
            file=sys.stderr,
        )
        return {}


def _apply_shared(shared: SharedConfig, yaml_cfg: dict) -> None:
    """Overlay ``shared:`` YAML keys onto *shared* in-place."""
    for key, val in yaml_cfg.get("shared", {}).items():
        if hasattr(shared, key):
            # YAML loads RGB lists as Python lists; PIL requires tuple or int.
            if isinstance(val, list):
                val = tuple(val)
            setattr(shared, key, val)


def _apply_section(obj: object, yaml_cfg: dict, section: str) -> None:
    """Overlay a named YAML section onto a config dataclass in-place."""
    for key, val in yaml_cfg.get(section, {}).items():
        if hasattr(obj, key):
            if isinstance(val, list):
                val = tuple(val)
            setattr(obj, key, val)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # ------------------------------------------------------------------
    # 0. Run-lock (prevent concurrent instances)
    # ------------------------------------------------------------------
    acquire_runlock()

    # ------------------------------------------------------------------
    # 1. CLI
    # ------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Android TV Background Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--gen",
        metavar="NAME",
        help=(
            "Force a specific generator to run "
            "(tmdb | jellyfin | plex | plexfriend | radarrsonarr | trakt | lidarr | steam). "
            "By default all configured services run automatically."
        ),
    )
    args = parser.parse_args()
    forced: str | None = args.gen.lower() if args.gen else None

    # ------------------------------------------------------------------
    # 2. Environment & config
    # ------------------------------------------------------------------
    load_dotenv(verbose=False)
    setup_logging()

    _pkg_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_pkg_dir)
    config_path = os.getenv(
        "CONFIG_FILE",
        os.path.join(_project_root, "config/config.yaml"),
    )
    yaml_cfg = _load_yaml(config_path)

    source_dir = _project_root

    # ------------------------------------------------------------------
    # 3. Shared / renderer / font
    # ------------------------------------------------------------------
    shared = SharedConfig.from_env()
    _apply_shared(shared, yaml_cfg)

    renderer = make_renderer(shared)
    font_mgr = make_font_manager(shared)

    try:
        validate_source_files(source_dir, shared)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4. Generator dispatch
    # ------------------------------------------------------------------

    def should_run(name: str) -> bool:
        """True when no generator is forced, or the forced name matches."""
        return forced is None or forced == name

    def _abort(name: str, msg: str) -> None:
        print(f"{name}: {msg}", file=sys.stderr)
        sys.exit(1)

    ran_any = False

    # --- Jellyfin -------------------------------------------------------
    if should_run("jellyfin"):
        jf = JellyfinConfig.from_env()
        if jf.base_url and jf.token and jf.user_id:
            _apply_section(jf, yaml_cfg, "jellyfin")
            JellyfinGenerator(jf, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "jellyfin":
            _abort("Jellyfin", "JELLYFIN_BASEURL, JELLYFIN_TOKEN, and JELLYFIN_USER_ID are required.")

    # --- Plex ------------------------------------------------------------
    if should_run("plex"):
        px = PlexConfig.from_env()
        if px.base_url and px.token:
            _apply_section(px, yaml_cfg, "plex")
            PlexGenerator(px, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "plex":
            _abort("Plex", "PLEX_BASEURL and PLEX_TOKEN are required.")

    # --- Plex Friends ----------------------------------------------------
    if should_run("plexfriend"):
        plex_token = os.getenv("PLEX_TOKEN", "")
        friend_enabled = os.getenv("PLEX_FRIEND_ENABLED", "false").lower() == "true"
        if plex_token and (friend_enabled or forced == "plexfriend"):
            pf = PlexFriendConfig.from_env()
            _apply_section(pf, yaml_cfg, "plexfriend")
            PlexFriendGenerator(pf, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "plexfriend":
            _abort("PlexFriend", "PLEX_TOKEN is required and PLEX_FRIEND_ENABLED must be true.")

    # --- Radarr / Sonarr ------------------------------------------------
    if should_run("radarrsonarr"):
        rs = RadarrSonarrConfig.from_env()
        has_radarr = bool(rs.radarr_url and rs.radarr_api_key)
        has_sonarr = bool(rs.sonarr_url and rs.sonarr_api_key)
        if rs.tmdb_bearer_token and (has_radarr or has_sonarr):
            _apply_section(rs, yaml_cfg, "radarrsonarr")
            RadarrSonarrGenerator(rs, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "radarrsonarr":
            _abort(
                "RadarrSonarr",
                "TMDB_BEARER_TOKEN and at least one of RADARR_API_KEY or SONARR_API_KEY are required.",
            )

    # --- Trakt ----------------------------------------------------------
    if should_run("trakt"):
        tr = TraktConfig.from_env()
        if tr.api_key and tr.username and tr.list_name:
            _apply_section(tr, yaml_cfg, "trakt")
            TraktGenerator(tr, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "trakt":
            _abort("Trakt", "TRAKT_API_KEY, TRAKT_USERNAME, and TRAKT_LISTNAME are required.")

    # --- Lidarr ---------------------------------------------------------
    if should_run("lidarr"):
        li = LidarrConfig.from_env()
        if li.base_url and li.api_key:
            _apply_section(li, yaml_cfg, "lidarr")
            LidarrGenerator(li, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "lidarr":
            _abort("Lidarr", "LIDARR_URL and LIDARR_API_KEY are required.")

    # --- Steam ----------------------------------------------------------
    if should_run("steam"):
        st = SteamConfig.from_env()
        if st.api_key and st.user_id:
            _apply_section(st, yaml_cfg, "steam")
            SteamGenerator(st, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "steam":
            _abort("Steam", "STEAM_API_KEY and STEAM_USER_ID are required.")

    # --- TMDB (fallback or forced) --------------------------------------
    # Runs automatically only when no other generator ran, or when forced.
    if should_run("tmdb") and (not ran_any or forced == "tmdb"):
        tm = TMDBConfig.from_env()
        if tm.bearer_token:
            _apply_section(tm, yaml_cfg, "tmdb")
            TMDBGenerator(tm, shared, renderer, font_mgr, source_dir).run()
            ran_any = True
        elif forced == "tmdb":
            _abort("TMDB", "TMDB_BEARER_TOKEN is required. Add it to your .env file.")
        else:
            print(
                "No services detected and TMDB_BEARER_TOKEN is not set.\n"
                "Add credentials to .env — see .env.example and the README.",
                file=sys.stderr,
            )

    if not ran_any:
        print(
            "No generators ran.  Check that the required environment variables are set in .env.\n"
            "See .env.example and the README for details.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
