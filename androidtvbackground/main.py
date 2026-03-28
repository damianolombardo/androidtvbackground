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
import concurrent.futures
import logging
import os
import shutil
import sys

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("androidtvbg")

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
    load_dotenv(os.getenv("DOTENV_PATH") or None, verbose=False, override=False)
    setup_logging(os.getenv("LOG_DIR", "logs"))

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

    # ------------------------------------------------------------------
    # 4a. Optional collect directory — gather all service images in one place.
    # ------------------------------------------------------------------

    def _resolve_dir(raw: str) -> str:
        """Resolve a possibly-relative directory path to an absolute one."""
        base = os.getenv("BACKGROUNDS_BASE_DIR")
        if base and not os.path.isabs(raw):
            return os.path.join(base, os.path.relpath(raw, "backgrounds"))
        if not os.path.isabs(raw):
            return os.path.join(source_dir, raw)
        return raw

    collect_dir = _resolve_dir(shared.collect_dir) if shared.collect_dir else ""
    collect_staging = collect_dir + ".staging" if collect_dir else ""

    if collect_staging:
        # Prepare a fresh staging dir; the live collect_dir is untouched until
        # all services finish, keeping it consistent for readers throughout the run.
        if os.path.exists(collect_staging):
            shutil.rmtree(collect_staging)
        os.makedirs(collect_staging)

    def _collect(service_name: str, output_dir: str) -> None:
        """Copy every image from *output_dir* into the collect staging dir,
        prefixed with *service_name* so files from different services never collide."""
        if not collect_staging or not os.path.isdir(output_dir):
            return
        for fname in os.listdir(output_dir):
            src = os.path.join(output_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(collect_staging, f"{service_name}_{fname}"))
        logger.info("Staged %s images → %s", service_name, collect_staging)

    def _commit_collect() -> None:
        """Atomically replace the live collect dir with the staging dir."""
        if not collect_staging or not os.path.isdir(collect_staging):
            return
        if os.path.exists(collect_dir):
            shutil.rmtree(collect_dir)
        os.rename(collect_staging, collect_dir)
        logger.info("Collect dir committed → %s", collect_dir)

    # ------------------------------------------------------------------
    # 4b. Build per-service callables (each gets its own renderer/font_mgr
    #     since those objects carry mutable state).
    # ------------------------------------------------------------------

    def _make_runner(gen_class, cfg, yaml_section):
        """Capture cfg and return a zero-arg callable that runs the generator."""
        _apply_section(cfg, yaml_cfg, yaml_section)
        def _run():
            gen_class(cfg, shared, make_renderer(shared), make_font_manager(shared), source_dir).run()
        return _run

    # Collect (name, output_dir, callable) for all non-TMDB services that have credentials.
    service_runners: list[tuple[str, str, object]] = []

    # --- Jellyfin -------------------------------------------------------
    if should_run("jellyfin"):
        jf = JellyfinConfig.from_env()
        if jf.base_url and jf.token and jf.user_id:
            service_runners.append(("jellyfin", jf.output_dir, _make_runner(JellyfinGenerator, jf, "jellyfin")))
        elif forced == "jellyfin":
            _abort("Jellyfin", "JELLYFIN_BASEURL, JELLYFIN_TOKEN, and JELLYFIN_USER_ID are required.")

    # --- Plex ------------------------------------------------------------
    if should_run("plex"):
        px = PlexConfig.from_env()
        if px.base_url and px.token:
            service_runners.append(("plex", px.output_dir, _make_runner(PlexGenerator, px, "plex")))
        elif forced == "plex":
            _abort("Plex", "PLEX_BASEURL and PLEX_TOKEN are required.")

    # --- Plex Friends ----------------------------------------------------
    if should_run("plexfriend"):
        plex_token = os.getenv("PLEX_TOKEN", "")
        friend_enabled = os.getenv("PLEX_FRIEND_ENABLED", "false").lower() == "true"
        if plex_token and (friend_enabled or forced == "plexfriend"):
            pf = PlexFriendConfig.from_env()
            service_runners.append(("plexfriend", pf.output_dir, _make_runner(PlexFriendGenerator, pf, "plexfriend")))
        elif forced == "plexfriend":
            _abort("PlexFriend", "PLEX_TOKEN is required and PLEX_FRIEND_ENABLED must be true.")

    # --- Radarr / Sonarr ------------------------------------------------
    if should_run("radarrsonarr"):
        rs = RadarrSonarrConfig.from_env()
        has_radarr = bool(rs.radarr_url and rs.radarr_api_key)
        has_sonarr = bool(rs.sonarr_url and rs.sonarr_api_key)
        if rs.tmdb_bearer_token and (has_radarr or has_sonarr):
            service_runners.append(("radarrsonarr", rs.output_dir, _make_runner(RadarrSonarrGenerator, rs, "radarrsonarr")))
        elif forced == "radarrsonarr":
            _abort(
                "RadarrSonarr",
                "TMDB_BEARER_TOKEN and at least one of RADARR_API_KEY or SONARR_API_KEY are required.",
            )

    # --- Trakt ----------------------------------------------------------
    if should_run("trakt"):
        tr = TraktConfig.from_env()
        if tr.api_key and tr.username and tr.list_name:
            service_runners.append(("trakt", tr.output_dir, _make_runner(TraktGenerator, tr, "trakt")))
        elif forced == "trakt":
            _abort("Trakt", "TRAKT_API_KEY, TRAKT_USERNAME, and TRAKT_LISTNAME are required.")

    # --- Lidarr ---------------------------------------------------------
    if should_run("lidarr"):
        li = LidarrConfig.from_env()
        if li.base_url and li.api_key:
            service_runners.append(("lidarr", li.output_dir, _make_runner(LidarrGenerator, li, "lidarr")))
        elif forced == "lidarr":
            _abort("Lidarr", "LIDARR_URL and LIDARR_API_KEY are required.")

    # --- Steam ----------------------------------------------------------
    if should_run("steam"):
        st = SteamConfig.from_env()
        if st.api_key and st.user_id:
            service_runners.append(("steam", st.output_dir, _make_runner(SteamGenerator, st, "steam")))
        elif forced == "steam":
            _abort("Steam", "STEAM_API_KEY and STEAM_USER_ID are required.")

    # ------------------------------------------------------------------
    # 4c. Run all services concurrently (I/O-bound — threads give real
    #     speedup even under the GIL).
    # ------------------------------------------------------------------
    ran_any = False

    if service_runners:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(service_runners)) as pool:
            futures = {pool.submit(fn): (name, out_dir) for name, out_dir, fn in service_runners}
            for future in concurrent.futures.as_completed(futures):
                name, out_dir = futures[future]
                try:
                    future.result()
                    ran_any = True
                    _collect(name, _resolve_dir(out_dir))
                except Exception as exc:
                    print(f"ERROR [{name}]: {exc}", file=sys.stderr)

    # --- TMDB (fallback or forced) --------------------------------------
    # Runs automatically only when no other generator ran, or when forced.
    if should_run("tmdb") and (not ran_any or forced == "tmdb"):
        tm = TMDBConfig.from_env()
        if tm.bearer_token:
            _apply_section(tm, yaml_cfg, "tmdb")
            TMDBGenerator(tm, shared, make_renderer(shared), make_font_manager(shared), source_dir).run()
            ran_any = True
            _collect("tmdb", _resolve_dir(tm.output_dir))
        elif forced == "tmdb":
            _abort("TMDB", "TMDB_BEARER_TOKEN is required. Add it to your .env file.")
        else:
            print(
                "No services detected and TMDB_BEARER_TOKEN is not set.\n"
                "Add credentials to .env — see .env.example and the README.",
                file=sys.stderr,
            )

    _commit_collect()

    if not ran_any:
        print(
            "No generators ran.  Check that the required environment variables are set in .env.\n"
            "See .env.example and the README for details.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
