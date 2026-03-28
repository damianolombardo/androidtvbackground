"""Common classes and utilities for the Android TV Background Generator.

All background generation logic lives here.  ``main.py`` is the only file
that needs to be edited for normal use.

Public surface
--------------
Config dataclasses
    SharedConfig, TMDBConfig, JellyfinConfig, PlexConfig, PlexFriendConfig,
    RadarrSonarrConfig, TraktConfig, LidarrConfig, SteamConfig

Utilities
    FontManager, ImageUtils

Renderers (canvas-building strategies)
    BackgroundRenderer (abstract), OverlayRenderer, ColorRenderer

Generators (one per service)
    BaseGenerator (abstract), TMDBGenerator, JellyfinGenerator,
    PlexGenerator, PlexFriendGenerator, RadarrSonarrGenerator,
    TraktGenerator, LidarrGenerator, SteamGenerator

Logging
    setup_logging  — call once from ``main.py`` to enable file + console logging
"""

from __future__ import annotations

import atexit
import html as _html
import html.parser
import logging
import math
import os
import random
import shutil
import sys
import tempfile
import textwrap
import time
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from logging.handlers import RotatingFileHandler
from typing import Any

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# Module-level logger  (configured by setup_logging() called from main.py)
# ---------------------------------------------------------------------------

logger = logging.getLogger("androidtvbg")


# ---------------------------------------------------------------------------
# Layout constants — tuned for 3840×2160 canvas
# ---------------------------------------------------------------------------

_TEXT_LEFT: int = 210   # left margin for info/summary/metadata text
_INFO_Y: int    = 650   # info line vertical position
_SUMMARY_Y: int = 730   # summary/description vertical position
_TITLE_X: int   = 200   # title fallback x (when no logo)
_TITLE_Y: int   = 420   # title fallback y (when no logo)


# ---------------------------------------------------------------------------
# Run-lock (prevents concurrent executions)
# ---------------------------------------------------------------------------

_LOCK_FILE = os.path.join(tempfile.gettempdir(), "androidtvbg.lock")


def acquire_runlock() -> None:
    """Raise RuntimeError if another instance is already running."""
    if os.path.exists(_LOCK_FILE):
        with open(_LOCK_FILE) as _f:
            _pid = _f.read().strip()
        try:
            os.kill(int(_pid), 0)   # signal 0 = existence check
            raise RuntimeError(
                f"Another instance (PID {_pid}) is already running. "
                f"If this is wrong, delete {_LOCK_FILE} and retry."
            )
        except (ProcessLookupError, ValueError):
            pass   # stale lock — overwrite
    with open(_LOCK_FILE, "w") as _f:
        _f.write(str(os.getpid()))
    atexit.register(_release_runlock)


def _release_runlock() -> None:
    try:
        os.unlink(_LOCK_FILE)
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _parse_color_env(var: str):
    """Return None if unset, a string name, or an (R,G,B) tuple."""
    raw = os.getenv(var, "").strip()
    if not raw:
        return None
    if "," in raw:
        return tuple(int(x) for x in raw.split(","))
    return raw


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _HTMLTextExtractor(html.parser.HTMLParser):
    """Collect visible text from an HTML fragment, collapsing whitespace."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self._parts).split())


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities; collapse whitespace.

    Falls back to the original string if parsing fails.
    """
    if not text or "<" not in text:
        return text
    try:
        extractor = _HTMLTextExtractor()
        extractor.feed(text)
        return extractor.get_text() or text
    except Exception:
        return text


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SharedConfig:
    """Settings that apply to every generator.

    These control visual style, typography, and text layout.
    Override any field in ``main.py`` after calling ``from_env()``.
    """

    background_style: str = "color"   # "color" (blurred) or "overlay" (static canvas)

    # Font (primary; falls back through several open-source fonts automatically)
    font_url: str = "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Light.ttf"
    font_name: str = "Roboto-Light.ttf"

    # Text colours — colour name strings or (R, G, B) tuples
    main_color: Any = "white"
    info_color: Any = (150, 150, 150)
    summary_color: Any = "white"
    metadata_color: Any = "white"
    shadow_color: Any = "black"
    shadow_offset: int = 2

    # Summary wrapping
    max_summary_chars: int = 525
    max_summary_width: int = 2100

    # Output format / quality
    output_format: str = "jpg"   # "jpg", "png", or "webp"
    output_quality: int = 95

    # Vignette parameters (ColorRenderer)
    vignette_fade_ratio: float = 0.3
    vignette_fade_power: float = 2.5
    vignette_blur_radius: int = 60
    vignette_offset_bottom: int = 150

    # Extra pixels inserted between wrapped text lines
    line_spacing: int = 0

    # Canvas dimensions (color style only; overlay uses bckg.png size)
    canvas_width: int  = 3840
    canvas_height: int = 2160

    # Optional flat collection directory — when set, images from every service
    # are also copied here (prefixed with the service name) after each run.
    collect_dir: str = ""

    @classmethod
    def from_env(cls) -> "SharedConfig":
        return cls(
            background_style=os.getenv("BACKGROUND_STYLE", "color").lower(),
            output_format=os.getenv("OUTPUT_FORMAT", "jpg").lower(),
            output_quality=max(1, min(95, int(os.getenv("OUTPUT_QUALITY", "95")))),
            vignette_fade_ratio=float(os.getenv("VIGNETTE_FADE_RATIO", "0.3")),
            vignette_fade_power=float(os.getenv("VIGNETTE_FADE_POWER", "2.5")),
            vignette_blur_radius=int(os.getenv("VIGNETTE_BLUR_RADIUS", "60")),
            vignette_offset_bottom=int(os.getenv("VIGNETTE_OFFSET_BOTTOM", "150")),
            canvas_width=int(os.getenv("CANVAS_WIDTH", "3840")),
            canvas_height=int(os.getenv("CANVAS_HEIGHT", "2160")),
            collect_dir=os.getenv("COLLECT_DIR", ""),
        )


@dataclass
class TMDBConfig:
    """Configuration for the TMDB trending generator."""

    bearer_token: str = ""
    base_url: str = "https://api.themoviedb.org/3"
    img_base: str = "https://image.tmdb.org/t/p/original"
    language: str = "en-US"
    number_of_movies: int = 5
    number_of_tv_shows: int = 5
    max_age_days: int = 90
    output_dir: str = "backgrounds/tmdb"
    custom_text: str = "Now Trending on"

    # Content-filtering exclusion rules (set in main.py)
    tv_excluded_countries: list[str] = field(default_factory=list)
    tv_excluded_genres: dict[str, list[str]] = field(default_factory=dict)
    movie_excluded_countries: list[str] = field(default_factory=list)
    movie_excluded_genres: dict[str, list[str]] = field(default_factory=dict)
    excluded_keywords: list[str] = field(default_factory=list)

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "TMDBConfig":
        return cls(
            bearer_token=os.getenv("TMDB_BEARER_TOKEN", ""),
            base_url=os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3"),
            img_base=os.getenv("TMDB_IMG_BASE", "https://image.tmdb.org/t/p/original"),
            language=os.getenv("TMDB_LANGUAGE", "en-US"),
            number_of_movies=int(os.getenv("NUMBER_OF_MOVIES", "5")),
            number_of_tv_shows=int(os.getenv("NUMBER_OF_TV_SHOWS", "5")),
            max_age_days=int(os.getenv("MAX_AGE_DAYS", "90")),
            output_dir=os.getenv("OUTPUT_DIR", "backgrounds/tmdb"),
            custom_text=os.getenv("CUSTOM_TEXT", "Now Trending on"),
            main_color_override=_parse_color_env("TMDB_MAIN_COLOR"),
            info_color_override=_parse_color_env("TMDB_INFO_COLOR"),
            summary_color_override=_parse_color_env("TMDB_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("TMDB_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.bearer_token:
            raise ValueError(
                "TMDB_BEARER_TOKEN is required. Add it to your .env file."
            )


@dataclass
class JellyfinConfig:
    """Configuration for the Jellyfin generator."""

    base_url: str = ""
    token: str = ""
    user_id: str = ""
    output_dir: str = "backgrounds/jellyfin"

    order_by: str = "mix"           # "added", "aired", or "mix"
    download_movies: bool = True
    download_series: bool = True
    download_music: bool = False
    limit: int = 10
    debug: bool = False
    api_delay: float = 1.0

    logo_h_offset: int = 0
    logo_v_offset: int = 0

    added_label: str = "New or updated on"
    aired_label: str = "Recent release, available on"
    random_label: str = "Random pick from"
    default_label: str = "Now Available on"

    excluded_genres: list[str] = field(default_factory=list)
    excluded_tags: list[str] = field(default_factory=list)
    excluded_libraries: list[str] = field(default_factory=list)
    max_genres: int = 3

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "JellyfinConfig":
        return cls(
            base_url=os.getenv("JELLYFIN_BASEURL", ""),
            token=os.getenv("JELLYFIN_TOKEN", ""),
            user_id=os.getenv("JELLYFIN_USER_ID", ""),
            api_delay=max(0.0, float(os.getenv("JELLYFIN_API_DELAY", "1.0"))),
            main_color_override=_parse_color_env("JELLYFIN_MAIN_COLOR"),
            info_color_override=_parse_color_env("JELLYFIN_INFO_COLOR"),
            summary_color_override=_parse_color_env("JELLYFIN_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("JELLYFIN_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.base_url:
            raise ValueError("JELLYFIN_BASEURL is required.")
        if not self.token:
            raise ValueError("JELLYFIN_TOKEN is required.")
        if not self.user_id:
            raise ValueError("JELLYFIN_USER_ID is required.")


@dataclass
class PlexConfig:
    """Configuration for the Plex generator."""

    base_url: str = ""
    token: str = ""
    output_dir: str = "backgrounds/plex"

    order_by: str = "mix"
    download_movies: bool = True
    download_series: bool = True
    download_music: bool = False
    limit: int = 10
    debug: bool = False
    api_delay: float = 1.0

    logo_variant: str = "white"     # "white" or "color"
    logo_h_offset: int = 0
    logo_v_offset: int = 7

    added_label: str = "New or updated on"
    aired_label: str = "Recent release, available on"
    random_label: str = "Random pick from"
    default_label: str = "New or updated on"

    # Content filtering
    excluded_genres: list[str] = field(default_factory=list)
    excluded_tags: list[str] = field(default_factory=list)
    excluded_libraries: list[str] = field(default_factory=list)
    max_genres: int = 3

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "PlexConfig":
        return cls(
            base_url=os.getenv("PLEX_BASEURL", ""),
            token=os.getenv("PLEX_TOKEN", ""),
            api_delay=max(0.0, float(os.getenv("PLEX_API_DELAY", "1.0"))),
            main_color_override=_parse_color_env("PLEX_MAIN_COLOR"),
            info_color_override=_parse_color_env("PLEX_INFO_COLOR"),
            summary_color_override=_parse_color_env("PLEX_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("PLEX_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.base_url:
            raise ValueError("PLEX_BASEURL is required.")
        if not self.token:
            raise ValueError("PLEX_TOKEN is required.")


@dataclass
class PlexFriendConfig:
    """Configuration for the Plex-friend-library generator."""

    token: str = ""
    output_dir: str = "backgrounds/plexfriend"
    target_friend: str | None = None  # None = all shared friends

    order_by: str = "added"
    download_movies: bool = True
    download_series: bool = True
    limit: int = 5
    debug: bool = False
    api_delay: float = 1.0

    logo_variant: str = "white"
    logo_h_offset: int = 0
    logo_v_offset: int = 7

    added_label: str = "Now shared on"
    aired_label: str = "Recent release, shared on"
    random_label: str = "Shared on"
    default_label: str = "Now shared on"

    max_genres: int = 3

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "PlexFriendConfig":
        return cls(
            token=os.getenv("PLEX_TOKEN", ""),
            api_delay=max(0.0, float(os.getenv("PLEXFRIEND_API_DELAY", "1.0"))),
            main_color_override=_parse_color_env("PLEXFRIEND_MAIN_COLOR"),
            info_color_override=_parse_color_env("PLEXFRIEND_INFO_COLOR"),
            summary_color_override=_parse_color_env("PLEXFRIEND_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("PLEXFRIEND_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.token:
            raise ValueError("PLEX_TOKEN is required for PlexFriend.")


@dataclass
class RadarrSonarrConfig:
    """Configuration for the Radarr / Sonarr upcoming-releases generator."""

    radarr_url: str = ""
    radarr_api_key: str = ""
    sonarr_url: str = ""
    sonarr_api_key: str = ""

    tmdb_bearer_token: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_img_base: str = "https://image.tmdb.org/t/p/original"
    language: str = "en-US"

    days_ahead: int = 7
    output_dir: str = "backgrounds/radarrsonarr"
    logo_filename: str = "jellyfinlogo.png"

    movie_custom_text: str = "New movie coming soon on"
    tv_custom_text: str = "New episode coming soon on"

    # Rate limiting
    api_delay: float = 0.0

    # Content filtering
    excluded_genres: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    min_vote_average: float = 0.0
    max_genres: int = 3

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "RadarrSonarrConfig":
        return cls(
            radarr_url=os.getenv("RADARR_URL", ""),
            radarr_api_key=os.getenv("RADARR_API_KEY", ""),
            sonarr_url=os.getenv("SONARR_URL", ""),
            sonarr_api_key=os.getenv("SONARR_API_KEY", ""),
            tmdb_bearer_token=os.getenv("TMDB_BEARER_TOKEN", ""),
            tmdb_base_url=os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3"),
            tmdb_img_base=os.getenv("TMDB_IMG_BASE", "https://image.tmdb.org/t/p/original"),
            language=os.getenv("TMDB_LANGUAGE", "en-US"),
            days_ahead=int(os.getenv("DAYS_AHEAD", "7")),
            logo_filename=os.getenv("RADARR_SONARR_LOGO", "jellyfinlogo.png"),
            api_delay=max(0.0, float(os.getenv("RADARRSONARR_API_DELAY", "0.0"))),
            main_color_override=_parse_color_env("RADARRSONARR_MAIN_COLOR"),
            info_color_override=_parse_color_env("RADARRSONARR_INFO_COLOR"),
            summary_color_override=_parse_color_env("RADARRSONARR_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("RADARRSONARR_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.tmdb_bearer_token:
            raise ValueError("TMDB_BEARER_TOKEN is required for RadarrSonarr.")
        if not self.radarr_url and not self.sonarr_url:
            raise ValueError(
                "At least one of RADARR_URL or SONARR_URL is required."
            )


@dataclass
class TraktConfig:
    """Configuration for the Trakt list generator."""

    api_key: str = ""
    username: str = ""
    list_name: str = ""

    tmdb_bearer_token: str = ""
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_img_base: str = "https://image.tmdb.org/t/p/original"
    output_dir: str = "backgrounds/trakt"

    download_movies: bool = True
    download_series: bool = True
    limit: int = 0               # 0 = no limit
    custom_text: str = ""        # "" = derive from list_name
    api_delay: float = 0.0
    max_genres: int = 3

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "TraktConfig":
        return cls(
            api_key=os.getenv("TRAKT_API_KEY", ""),
            username=os.getenv("TRAKT_USERNAME", ""),
            list_name=os.getenv("TRAKT_LISTNAME", ""),
            tmdb_bearer_token=os.getenv("TMDB_BEARER_TOKEN", ""),
            tmdb_base_url=os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3"),
            tmdb_img_base=os.getenv("TMDB_IMG_BASE", "https://image.tmdb.org/t/p/original"),
            api_delay=max(0.0, float(os.getenv("TRAKT_API_DELAY", "0.0"))),
            main_color_override=_parse_color_env("TRAKT_MAIN_COLOR"),
            info_color_override=_parse_color_env("TRAKT_INFO_COLOR"),
            summary_color_override=_parse_color_env("TRAKT_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("TRAKT_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.api_key:
            raise ValueError("TRAKT_API_KEY is required.")
        if not self.username:
            raise ValueError("TRAKT_USERNAME is required.")
        if not self.list_name:
            raise ValueError("TRAKT_LISTNAME is required.")


@dataclass
class LidarrConfig:
    """Configuration for the Lidarr upcoming-albums generator."""

    base_url: str = ""
    api_key: str = ""
    days_ahead: int = 30
    output_dir: str = "backgrounds/lidarr"
    logo_filename: str = "lidarrlogo.png"
    custom_text: str = "New album coming soon on"
    api_delay: float = 0.5

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "LidarrConfig":
        return cls(
            base_url=os.getenv("LIDARR_URL", "").rstrip("/"),
            api_key=os.getenv("LIDARR_API_KEY", ""),
            days_ahead=int(os.getenv("DAYS_AHEAD", "30")),
            logo_filename=os.getenv("LIDARR_LOGO", "lidarrlogo.png"),
            api_delay=max(0.0, float(os.getenv("LIDARR_API_DELAY", "0.5"))),
            main_color_override=_parse_color_env("LIDARR_MAIN_COLOR"),
            info_color_override=_parse_color_env("LIDARR_INFO_COLOR"),
            summary_color_override=_parse_color_env("LIDARR_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("LIDARR_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.base_url:
            raise ValueError("LIDARR_URL is required.")
        if not self.api_key:
            raise ValueError("LIDARR_API_KEY is required.")


@dataclass
class SteamConfig:
    """Configuration for the Steam library generator."""

    api_key: str = ""
    user_id: str = ""

    recently_played_count: int = 5
    unplayed_count: int = 5       # formerly recently_purchased_count
    random_count: int = 5

    output_dir: str = "backgrounds/steam"
    logo_filename: str = "steamlogo.png"

    recently_played_label: str = "Recently played on"
    unplayed_label: str = "Waiting to be played on"   # formerly recently_purchased_label
    random_label: str = "From your library on"

    api_delay: float = 0.5

    # Per-service colour overrides (None = inherit from SharedConfig)
    main_color_override: Any = None
    info_color_override: Any = None
    summary_color_override: Any = None
    metadata_color_override: Any = None

    @classmethod
    def from_env(cls) -> "SteamConfig":
        return cls(
            api_key=os.getenv("STEAM_API_KEY", ""),
            user_id=os.getenv("STEAM_USER_ID", ""),
            logo_filename=os.getenv("STEAM_LOGO", "steamlogo.png"),
            api_delay=max(0.0, float(os.getenv("STEAM_API_DELAY", "0.5"))),
            main_color_override=_parse_color_env("STEAM_MAIN_COLOR"),
            info_color_override=_parse_color_env("STEAM_INFO_COLOR"),
            summary_color_override=_parse_color_env("STEAM_SUMMARY_COLOR"),
            metadata_color_override=_parse_color_env("STEAM_METADATA_COLOR"),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` if required fields are missing."""
        if not self.api_key:
            raise ValueError("STEAM_API_KEY is required.")
        if not self.user_id:
            raise ValueError("STEAM_USER_ID is required.")


# ---------------------------------------------------------------------------
# Font management
# ---------------------------------------------------------------------------

class FontManager:
    """Download and cache fonts, with an automatic fallback chain.

    Primary → Roboto-Light → OpenSans-Light → Lato-Light → Poppins-Light.
    Only fonts that fail to download are skipped.
    """

    _FALLBACK_CHAIN: list[tuple[str, str]] = [
        (
            "https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Light.ttf",
            "Roboto-Light.ttf",
        ),
        (
            "https://github.com/googlefonts/opensans/raw/main/fonts/ttf/OpenSans-Light.ttf",
            "OpenSans-Light.ttf",
        ),
        (
            "https://github.com/googlefonts/lato/raw/main/fonts/ttf/Lato-Light.ttf",
            "Lato-Light.ttf",
        ),
        (
            "https://github.com/googlefonts/poppins/raw/main/fonts/ttf/Poppins-Light.ttf",
            "Poppins-Light.ttf",
        ),
    ]

    def __init__(self, user_url: str = "", user_name: str = "") -> None:
        self._user_url = user_url
        self._user_name = user_name
        self._path: str | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ensure_font(self) -> None:
        """Download the font (if needed) and resolve ``self._path``."""
        # 1. Try the user-supplied font
        if self._user_url and self._user_name:
            if self._try_download(self._user_url, self._user_name):
                self._path = self._user_name
                return

        # 2. Work through the fallback chain
        for url, name in self._FALLBACK_CHAIN:
            if self._user_url and url == self._user_url:
                continue  # already attempted
            if self._try_download(url, name):
                self._path = name
                return

        raise RuntimeError(
            "No font could be downloaded. Cannot generate backgrounds."
        )

    def get_font(self, size: int) -> ImageFont.FreeTypeFont:
        """Return a ``FreeTypeFont`` at *size* pt.  Call ``ensure_font`` first."""
        if self._path is None:
            raise RuntimeError("Font not initialised — call ensure_font() first.")
        return ImageFont.truetype(self._path, size=size)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_download(url: str, path: str) -> bool:
        if os.path.exists(path):
            return True
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                with open(path, "wb") as fh:
                    fh.write(r.content)
                return True
            logger.debug("Font download HTTP %s: %s", r.status_code, url)
        except Exception:  # noqa: BLE001
            logger.debug("Font download failed: %s", url, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Image utilities
# ---------------------------------------------------------------------------

class ImageUtils:
    """Static helpers for image manipulation and text processing."""

    @staticmethod
    def resize_to_height(image: Image.Image, height: int) -> Image.Image:
        ratio = height / image.height
        return image.resize((int(image.width * ratio), height))

    @staticmethod
    def resize_logo(
        image: Image.Image, max_width: int, max_height: int
    ) -> Image.Image:
        aspect = image.width / image.height
        new_w = min(max_width, int(max_height * aspect))
        new_h = int(new_w / aspect)
        if new_h > max_height:
            new_h = max_height
            new_w = int(new_h * aspect)
        return image.resize((new_w, new_h))

    @staticmethod
    def clean_filename(name: str) -> str:
        norm = unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode()
        return "".join(c if c.isalnum() or c in "._-" else "_" for c in norm)

    @staticmethod
    def truncate_summary(text: str, max_chars: int) -> str:
        text = text or ""
        try:
            return textwrap.shorten(text, width=max_chars, placeholder="...")
        except ValueError:
            return "..."

    @staticmethod
    def wrap_by_pixel_width(
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
        draw: ImageDraw.ImageDraw,
    ) -> list[str]:
        """Word-wrap *text* so each line fits within *max_width* pixels."""
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            if draw.textlength(test, font=font) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                if draw.textlength(word, font=font) > max_width:
                    # Force-split a single word that is too long
                    part = ""
                    for ch in word:
                        if draw.textlength(part + ch, font=font) <= max_width:
                            part += ch
                        else:
                            lines.append(part)
                            part = ch
                    current = part
                else:
                    current = word
        if current:
            lines.append(current)
        return lines


# ---------------------------------------------------------------------------
# Background renderers
# ---------------------------------------------------------------------------

class BackgroundRenderer(ABC):
    """Strategy interface: build a 3840×2160 canvas from a backdrop image."""

    @abstractmethod
    def build_canvas(
        self, backdrop: Image.Image, source_dir: str, shared: "SharedConfig | None" = None
    ) -> Image.Image:
        """Return the composited canvas (RGB, 3840×2160)."""


class OverlayRenderer(BackgroundRenderer):
    """Static canvas style using ``bckg.png`` + ``overlay.png``."""

    def build_canvas(
        self, backdrop: Image.Image, source_dir: str, shared: "SharedConfig | None" = None
    ) -> Image.Image:
        bckg = Image.open(os.path.join(source_dir, "bckg.png"))
        overlay = Image.open(os.path.join(source_dir, "overlay.png"))
        resized = ImageUtils.resize_to_height(backdrop, 1500)
        bckg.paste(resized, (1175, 0))
        bckg.paste(overlay, (1175, 0), overlay)
        return bckg


class ColorRenderer(BackgroundRenderer):
    """Dynamic blurred-and-vignette canvas style."""

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _vignette_mask(
        height: int,
        width: int,
        fade_ratio: float = 0.3,
        fade_power: float = 2.5,
        position: str = "bottom-left",
        offset_left: int = 0,
        offset_bottom: int = 150,
    ) -> Image.Image:
        y, x = np.ogrid[0:height, 0:width]
        rx = width * fade_ratio
        ry = height * fade_ratio

        dist_x = np.ones_like(x, dtype=np.float32)
        dist_y = np.ones_like(y, dtype=np.float32)

        if "left" in position:
            dist_x = np.clip((x - offset_left) / rx, 0, 1)
        elif "right" in position:
            dist_x = np.clip((width - x) / rx, 0, 1)

        if "top" in position:
            dist_y = np.clip(y / ry, 0, 1)
        elif "bottom" in position:
            dist_y = np.clip((height - y - offset_bottom) / ry, 0, 1)

        has_h = any(d in position for d in ("left", "right"))
        has_v = any(d in position for d in ("top", "bottom"))
        alpha = np.minimum(dist_x, dist_y) if (has_h and has_v) else dist_x * dist_y
        return Image.fromarray((alpha ** fade_power * 255).astype(np.uint8))

    @staticmethod
    def _blurry_bg(
        image: Image.Image,
        size: tuple[int, int] = (3840, 2160),
        blur_radius: int = 800,
        dither: int = 16,
    ) -> Image.Image:
        bg = image.resize(size, Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(radius=blur_radius)
        )
        arr = np.array(bg).astype(np.float32)
        noise = np.random.uniform(-dither, dither, arr.shape)
        return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))

    # ------------------------------------------------------------------
    # Strategy implementation
    # ------------------------------------------------------------------

    def build_canvas(
        self,
        backdrop: Image.Image,
        source_dir: str,  # unused for color style
        shared: "SharedConfig | None" = None,
        target_width: int = 3000,
    ) -> Image.Image:
        img = backdrop.convert("RGB")

        # Resolve canvas dimensions
        cw = shared.canvas_width  if shared else 3840
        ch = shared.canvas_height if shared else 2160

        # Blurred, darkened background
        blurred = self._blurry_bg(img, size=(cw, ch))
        arr = (np.array(blurred).astype(np.float32) * 0.4).clip(0, 255).astype(np.uint8)
        canvas = Image.new("RGBA", blurred.size, (0, 0, 0, 255))
        canvas.paste(Image.fromarray(arr), (0, 0))

        # Resolve vignette parameters
        fade_ratio    = shared.vignette_fade_ratio    if shared else 0.3
        fade_power    = shared.vignette_fade_power    if shared else 2.5
        blur_radius   = shared.vignette_blur_radius   if shared else 60
        offset_bottom = shared.vignette_offset_bottom if shared else 150

        # Resize backdrop to target width and apply vignette
        w_pct = target_width / img.width
        new_size = (target_width, int(img.height * w_pct))
        fg = img.resize(new_size, Image.LANCZOS).convert("RGBA")
        mask = self._vignette_mask(
            fg.height, fg.width,
            fade_ratio=fade_ratio,
            fade_power=fade_power,
            offset_bottom=offset_bottom,
        )
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        fg.putalpha(mask)
        canvas.paste(fg, (cw - fg.width, 0), fg)

        return canvas.convert("RGB")


# ---------------------------------------------------------------------------
# TMDB API client
# ---------------------------------------------------------------------------

class TMDBClient:
    """All interactions with the TMDB REST API."""

    def __init__(self, cfg: TMDBConfig) -> None:
        self._cfg = cfg
        self._headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {cfg.bearer_token}",
        }
        self._movie_genres: dict[int, str] = {}
        self._tv_genres: dict[int, str] = {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = requests.get(url, headers=self._headers, params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Genre cache
    # ------------------------------------------------------------------

    def load_genres(self) -> None:
        base, lang = self._cfg.base_url, self._cfg.language
        self._movie_genres = {
            g["id"]: g["name"]
            for g in self._get(f"{base}/genre/movie/list?language={lang}").get("genres", [])
        }
        self._tv_genres = {
            g["id"]: g["name"]
            for g in self._get(f"{base}/genre/tv/list?language={lang}").get("genres", [])
        }

    @property
    def movie_genres(self) -> dict[int, str]:
        return self._movie_genres

    @property
    def tv_genres(self) -> dict[int, str]:
        return self._tv_genres

    # ------------------------------------------------------------------
    # Detail endpoints
    # ------------------------------------------------------------------

    def movie_details(self, movie_id: int) -> dict:
        return self._get(
            f"{self._cfg.base_url}/movie/{movie_id}?language={self._cfg.language}"
        )

    def tv_details(self, tv_id: int) -> dict:
        return self._get(
            f"{self._cfg.base_url}/tv/{tv_id}?language={self._cfg.language}"
        )

    def movie_keywords(self, movie_id: int) -> list[str]:
        try:
            return [
                kw["name"].lower()
                for kw in self._get(
                    f"{self._cfg.base_url}/movie/{movie_id}/keywords"
                ).get("keywords", [])
            ]
        except Exception:  # noqa: BLE001
            logger.debug("TMDB: movie_keywords failed for id=%s", movie_id, exc_info=True)
            return []

    def tv_keywords(self, tv_id: int) -> list[str]:
        try:
            return [
                kw["name"].lower()
                for kw in self._get(
                    f"{self._cfg.base_url}/tv/{tv_id}/keywords"
                ).get("results", [])
            ]
        except Exception:  # noqa: BLE001
            logger.debug("TMDB: tv_keywords failed for id=%s", tv_id, exc_info=True)
            return []

    def find_by_tvdb(self, tvdb_id: int) -> int | None:
        try:
            result = self._get(
                f"{self._cfg.base_url}/find/{tvdb_id}",
                params={"external_source": "tvdb_id", "language": self._cfg.language},
            )
            tv = result.get("tv_results", [])
            return tv[0]["id"] if tv else None
        except Exception:  # noqa: BLE001
            logger.debug("TMDB: find_by_tvdb failed for tvdb_id=%s", tvdb_id, exc_info=True)
            return None

    def get_logo(self, media_type: str, media_id: int) -> str | None:
        """Return the TMDB file path of the best logo, or ``None``."""
        base, lang = self._cfg.base_url, self._cfg.language
        try:
            data = self._get(f"{base}/{media_type}/{media_id}/images?language={lang}")
        except Exception:  # noqa: BLE001
            logger.debug(
                "TMDB: logo images fetch failed for %s/%s", media_type, media_id, exc_info=True
            )
            return None

        logos: list[dict] = data.get("logos", [])
        if not logos:
            try:
                fb = self._get(f"{base}/{media_type}/{media_id}/images?language=en")
                logos_en = fb.get("logos", [])
                if logos_en:
                    return sorted(logos_en, key=lambda lo: lo.get("vote_average", 0), reverse=True)[0]["file_path"]
            except Exception:  # noqa: BLE001
                logger.debug(
                    "TMDB: logo fallback fetch failed for %s/%s", media_type, media_id, exc_info=True
                )
            return None

        lang_match = [lo for lo in logos if lo.get("iso_639_1") == lang.split("-")[0]]
        pool = lang_match if lang_match else logos
        return sorted(pool, key=lambda lo: lo.get("vote_average", 0), reverse=True)[0]["file_path"]

    def fetch_trending_movies(self, count: int) -> list[dict]:
        return self._get(
            f"{self._cfg.base_url}/trending/movie/week?language={self._cfg.language}"
        ).get("results", [])[:count]

    def fetch_trending_tv(self, count: int) -> list[dict]:
        return self._get(
            f"{self._cfg.base_url}/trending/tv/week?language={self._cfg.language}"
        ).get("results", [])[:count]

    def media_details(self, tmdb_id: int, is_movie: bool) -> dict:
        mt = "movie" if is_movie else "tv"
        return self._get(
            f"{self._cfg.base_url}/{mt}/{tmdb_id}?language={self._cfg.language}"
        )


# ---------------------------------------------------------------------------
# TMDB content filter
# ---------------------------------------------------------------------------

class TMDBContentFilter:
    """Apply country / genre / keyword exclusion rules to TMDB results."""

    def __init__(self, client: TMDBClient, cfg: TMDBConfig) -> None:
        self._client = client
        self._max_air = datetime.now() - timedelta(days=cfg.max_age_days)
        self._tv_exc_c = [c.lower() for c in cfg.tv_excluded_countries]
        self._tv_exc_g = cfg.tv_excluded_genres
        self._mv_exc_c = [c.lower() for c in cfg.movie_excluded_countries]
        self._mv_exc_g = cfg.movie_excluded_genres
        self._kw = cfg.excluded_keywords

    def _country_genre_blocked(
        self,
        countries: list[str],
        genres: list[str],
        exc_countries: list[str],
        exc_genres: dict[str, list[str]],
    ) -> bool:
        for c in countries:
            if c in exc_countries:
                blocked = exc_genres.get(c, [])
                if blocked == ["*"] or any(g in blocked for g in genres):
                    return True
        return False

    def exclude_movie(self, movie: dict) -> bool:
        countries = [c.lower() for c in movie.get("origin_country", [])]
        genres = [
            self._client.movie_genres.get(gid, "")
            for gid in movie.get("genre_ids", [])
        ]
        if self._country_genre_blocked(countries, genres, self._mv_exc_c, self._mv_exc_g):
            return True
        if self._kw and any(kw in self._client.movie_keywords(movie["id"]) for kw in self._kw):
            return True
        date_str = movie.get("release_date", "")
        if date_str:
            try:
                if datetime.strptime(date_str, "%Y-%m-%d") < self._max_air:
                    return True
            except ValueError:
                pass
        return False

    def exclude_tv(self, show: dict) -> bool:
        countries = [c.lower() for c in show.get("origin_country", [])]
        genres = [
            self._client.tv_genres.get(gid, "")
            for gid in show.get("genre_ids", [])
        ]
        if self._country_genre_blocked(countries, genres, self._tv_exc_c, self._tv_exc_g):
            return True
        if self._kw and any(kw in self._client.tv_keywords(show["id"]) for kw in self._kw):
            return True
        details = self._client.tv_details(show["id"])
        last = details.get("last_air_date", "")
        if last:
            try:
                d = datetime.strptime(last, "%Y-%m-%d")
                if d < self._max_air:
                    return True
            except ValueError:
                pass
        return False


# ---------------------------------------------------------------------------
# Base generator
# ---------------------------------------------------------------------------

class BaseGenerator(ABC):
    """Abstract base for all background generators.

    Provides shared drawing helpers used by every subclass.
    """

    _STAGING_SUFFIX = ".staging"

    def __init__(
        self,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        self._shared = shared
        self._renderer = renderer
        self._fonts = font_manager
        self._source_dir = source_dir
        self._staging_dir: str = ""
        # Run-summary counters
        self._saved: int = 0
        self._skipped: int = 0
        self._errors: int = 0

    @abstractmethod
    def run(self) -> None:
        """Execute the full pipeline for this service."""

    # ------------------------------------------------------------------
    # Shared utilities
    # ------------------------------------------------------------------

    def _prepare_output_dir(self, output_dir: str) -> str:
        """Create a fresh staging directory; return its path."""
        base = os.getenv("BACKGROUNDS_BASE_DIR")
        if base and not os.path.isabs(output_dir):
            output_dir = os.path.join(base, os.path.relpath(output_dir, "backgrounds"))
        staging = output_dir + self._STAGING_SUFFIX
        if os.path.exists(staging):
            shutil.rmtree(staging)
        os.makedirs(staging)
        self._staging_dir = staging
        return staging

    def _commit_output(self, staging: str) -> None:
        """Atomically replace the real output dir with the staging dir."""
        final = staging[: -len(self._STAGING_SUFFIX)]
        if os.path.exists(final):
            shutil.rmtree(final)
        os.rename(staging, final)

    def _abort_output(self, staging: str) -> None:
        """Clean up staging dir on failure."""
        if os.path.exists(staging):
            shutil.rmtree(staging)

    def _save_canvas(self, canvas: Image.Image, directory: str, stem: str) -> str:
        """Save *canvas* to *directory*/*stem*.<ext> using configured format."""
        ext = self._shared.output_format.lower()
        fmt_map = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}
        fmt = fmt_map.get(ext, "JPEG")
        path = os.path.join(directory, f"{stem}.{ext}")
        canvas.convert("RGB").save(path, fmt, quality=self._shared.output_quality)
        return path

    def _color(self, override: Any, shared_attr: str) -> Any:
        """Return *override* when set; otherwise the matching SharedConfig attr."""
        return override if override is not None else getattr(self._shared, shared_attr)

    def _log_summary(self, tag: str) -> None:
        if self._errors:
            logger.warning(
                "[%s] Done — %d saved, %d skipped, %d errors",
                tag, self._saved, self._skipped, self._errors,
            )
        else:
            logger.info(
                "[%s] Done — %d saved, %d skipped",
                tag, self._saved, self._skipped,
            )

    def _draw_shadow_text(
        self,
        draw: ImageDraw.ImageDraw,
        pos: tuple[int, int],
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: Any,
    ) -> None:
        so = self._shared.shadow_offset
        sc = self._shared.shadow_color
        ls = self._shared.line_spacing
        if ls and "\n" in text:
            x, y = pos
            for line in text.split("\n"):
                draw.text((x + so, y + so), line, font=font, fill=sc)
                draw.text((x, y), line, font=font, fill=fill)
                lh = font.getbbox(line)[3]
                y += lh + ls
        else:
            draw.text((pos[0] + so, pos[1] + so), text, font=font, fill=sc)
            draw.text(pos, text, font=font, fill=fill)

    def _wrap(
        self,
        text: str,
        font: ImageFont.FreeTypeFont,
        draw: ImageDraw.ImageDraw,
    ) -> list[str]:
        return ImageUtils.wrap_by_pixel_width(
            text, font, self._shared.max_summary_width, draw
        )

    def _truncate(self, text: str) -> str:
        return ImageUtils.truncate_summary(_strip_html(text), self._shared.max_summary_chars)

    @staticmethod
    def _fetch_with_retry(
        url: str,
        max_attempts: int = 3,
        timeout: int = 10,
        headers: dict | None = None,
    ) -> "requests.Response | None":
        """GET *url* with exponential backoff; return Response or None."""
        for attempt in range(max_attempts):
            try:
                r = requests.get(url, headers=headers, timeout=timeout)
                if 200 <= r.status_code < 300:
                    return r
                if r.status_code in (401, 403):
                    logger.warning(
                        "HTTP %s (auth error) — not retrying: %s", r.status_code, url
                    )
                    return None  # fast fail
                logger.debug("HTTP %s on attempt %d: %s", r.status_code, attempt + 1, url)
            except requests.RequestException as exc:
                logger.debug("Request error attempt %d: %s — %s", attempt + 1, url, exc)
            if attempt < max_attempts - 1:
                time.sleep(2 ** attempt)   # 1s, 2s
        return None

    @staticmethod
    def _fetch_image(url: str) -> Image.Image | None:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return Image.open(BytesIO(r.content))
            logger.debug("Image fetch HTTP %s: %s", r.status_code, url)
        except Exception:  # noqa: BLE001
            logger.debug("Image fetch failed: %s", url, exc_info=True)
        return None

    def _text_block_y(
        self,
        upper_element_bottom: int,
        ft_info: ImageFont.FreeTypeFont,
        ft_over: ImageFont.FreeTypeFont,
        info_text: str,
        summary_text: str,
        draw: ImageDraw.ImageDraw,
    ) -> tuple[int, int, int]:
        """Return (info_y, summary_y, label_y) anchored below upper_element_bottom."""
        PAD_AFTER_LOGO = 25
        PAD_BETWEEN    = 10
        PAD_BEFORE_LBL = 30

        info_y = max(upper_element_bottom + PAD_AFTER_LOGO, 500)

        if info_text:
            info_h = draw.textbbox((0, 0), info_text, font=ft_info)[3]
        else:
            info_h = 0

        summary_y = info_y + info_h + PAD_BETWEEN

        if summary_text:
            sum_h = draw.textbbox((0, 0), summary_text, font=ft_over)[3]
        else:
            sum_h = 0

        label_y = summary_y + sum_h + (PAD_BEFORE_LBL if summary_text else PAD_BETWEEN)
        return info_y, summary_y, label_y

    @staticmethod
    def _format_runtime(minutes: int) -> str:
        if not minutes:
            return ""
        h, m = divmod(minutes, 60)
        return f"{h}h{m:02d}min"


# ---------------------------------------------------------------------------
# TMDB generator
# ---------------------------------------------------------------------------

class TMDBGenerator(BaseGenerator):
    """Generate backgrounds from TMDB weekly trending content."""

    def __init__(
        self,
        cfg: TMDBConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._client = TMDBClient(cfg)
        self._filter = TMDBContentFilter(self._client, cfg)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info(
            "TMDB Background Generator — style=%s  movies=%s  tv=%s  output=%s",
            self._shared.background_style,
            self._cfg.number_of_movies,
            self._cfg.number_of_tv_shows,
            self._cfg.output_dir,
        )
        self._fonts.ensure_font()
        self._client.load_genres()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            self._process_movies()
            self._process_tv()
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("TMDB")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render_item(
        self,
        backdrop_url: str,
        title: str,
        tmdb_id: int,
        media_type: str,
        overview: str,
        genre: str,
        year: str,
        rating: float,
        runtime_text: str,
    ) -> None:
        backdrop = self._fetch_image(backdrop_url)
        if backdrop is None:
            logger.warning("TMDB: no backdrop for '%s' — skipping", title)
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(backdrop, self._source_dir, shared=self._shared)
        draw = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(50)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        info_pos    = (_TEXT_LEFT, _INFO_Y)
        summary_pos = (_TEXT_LEFT, _SUMMARY_Y)

        # Info bar
        year_t = year[:7] if len(year) > 7 else year
        info_t = f"{genre}  \u2022  {year_t}  \u2022  {runtime_text}  \u2022  TMDB: {rating}"
        self._draw_shadow_text(
            draw, info_pos, info_t, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        # Overview
        overview_t = self._truncate(overview)
        wrapped    = "\n".join(self._wrap(overview_t, ft_over, draw))
        self._draw_shadow_text(
            draw, summary_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Dynamic custom text position
        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = summary_pos[1] + (bbox[3] - bbox[1]) + 30
        self._draw_shadow_text(
            draw, (210, cust_y), self._cfg.custom_text, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        # Service logo (tmdblogo.png) beside custom text
        logo_path = os.path.join(self._source_dir, "tmdblogo.png")
        if os.path.exists(logo_path):
            tmdb_logo = Image.open(logo_path)
            canvas.paste(tmdb_logo, (680, cust_y + 20), tmdb_logo)

        # Media logo or title fallback
        logo_fp = self._client.get_logo(media_type, tmdb_id)
        logo_drawn = False
        if logo_fp:
            logo_img = self._fetch_image(f"{self._cfg.img_base}{logo_fp}")
            if logo_img:
                resized = ImageUtils.resize_logo(logo_img, 1000, 500).convert("RGBA")
                canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
                logo_drawn = True

        if not logo_drawn:
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), title, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        fname = self._save_canvas(canvas, self._staging_dir, ImageUtils.clean_filename(title))
        logger.info("TMDB: saved %s", fname)
        self._saved += 1

    def _process_movies(self) -> None:
        all_movies = self._client.fetch_trending_movies(self._cfg.number_of_movies + 10)
        accepted = 0
        for movie in all_movies:
            if accepted >= self._cfg.number_of_movies:
                break
            if self._filter.exclude_movie(movie):
                logger.debug("TMDB: excluded movie '%s'", movie.get("title"))
                continue
            details = self._client.movie_details(movie["id"])
            genre   = ", ".join(
                self._client.movie_genres.get(gid, "")
                for gid in movie.get("genre_ids", [])
                if self._client.movie_genres.get(gid)
            )
            runtime = self._format_runtime(details.get("runtime", 0))
            if not movie.get("backdrop_path"):
                continue
            self._render_item(
                backdrop_url=f"{self._cfg.img_base}{movie['backdrop_path']}",
                title=movie["title"],
                tmdb_id=movie["id"],
                media_type="movie",
                overview=movie.get("overview", ""),
                genre=genre,
                year=movie.get("release_date", ""),
                rating=round(movie.get("vote_average", 0), 1),
                runtime_text=runtime,
            )
            accepted += 1

    def _process_tv(self) -> None:
        all_shows = self._client.fetch_trending_tv(self._cfg.number_of_tv_shows + 10)
        accepted = 0
        for show in all_shows:
            if accepted >= self._cfg.number_of_tv_shows:
                break
            if self._filter.exclude_tv(show):
                logger.debug("TMDB: excluded show '%s'", show.get("name"))
                continue
            details = self._client.tv_details(show["id"])
            seasons = details.get("number_of_seasons", 0)
            genre   = ", ".join(
                self._client.tv_genres.get(gid, "")
                for gid in show.get("genre_ids", [])
                if self._client.tv_genres.get(gid)
            )
            runtime = f"{seasons} {'Season' if seasons == 1 else 'Seasons'}"
            if not show.get("backdrop_path"):
                continue
            title = show["name"][:38]
            self._render_item(
                backdrop_url=f"{self._cfg.img_base}{show['backdrop_path']}",
                title=title,
                tmdb_id=show["id"],
                media_type="tv",
                overview=show.get("overview", ""),
                genre=genre,
                year=show.get("first_air_date", ""),
                rating=round(show.get("vote_average", 0), 1),
                runtime_text=runtime,
            )
            accepted += 1


# ---------------------------------------------------------------------------
# Jellyfin generator
# ---------------------------------------------------------------------------

class JellyfinGenerator(BaseGenerator):
    """Generate backgrounds from a Jellyfin media server."""

    def __init__(
        self,
        cfg: JellyfinConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._excluded_paths: set[str] = set()
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("[Jellyfin] Connecting …")
        try:
            r = requests.get(
                f"{self._cfg.base_url}/Users/{self._cfg.user_id}",
                headers={"X-Emby-Token": self._cfg.token},
            )
            r.raise_for_status()
            logger.info("[Jellyfin] Connected. User: %s", r.json().get("Name"))
        except requests.RequestException as exc:
            logger.error("[Jellyfin] Connection failed: %s", exc, exc_info=True)
            return

        self._fonts.ensure_font()
        self._excluded_paths = self._get_excluded_paths()
        self._service_logo   = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            if self._cfg.download_movies:
                self._run_type("Movie")
            if self._cfg.download_series:
                self._run_type("Series")
            if self._cfg.download_music:
                self._run_type("MusicAlbum")
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("Jellyfin")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _get_excluded_paths(self) -> set[str]:
        if not self._cfg.excluded_libraries:
            return set()
        try:
            r = requests.get(
                f"{self._cfg.base_url}/Library/VirtualFolders",
                headers={"X-Emby-Token": self._cfg.token},
            )
            if r.status_code == 200:
                locs = [
                    lib["Locations"]
                    for lib in r.json()
                    if lib["Name"] in self._cfg.excluded_libraries
                ]
                return {loc for sub in locs for loc in sub}
        except Exception:  # noqa: BLE001
            logger.debug("[Jellyfin] Library paths fetch failed", exc_info=True)
        return set()

    def _load_service_logo(self) -> Image.Image | None:
        path = os.path.join(self._source_dir, "jellyfinlogo.png")
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.warning("[Jellyfin] jellyfinlogo.png not found")
            return None

    def _fetch_items(self, sort_type: str, count: int, media_type: str) -> list[dict]:
        headers = {"X-Emby-Token": self._cfg.token}
        fields  = "Path,Overview,Genres,CommunityRating,PremiereDate,Tags,OfficialRating,RunTimeTicks,AlbumArtist,ChildCount"

        if sort_type == "random":
            params: dict = {
                "SortBy": "Random",
                "Limit": count * 5,
                "IncludeItemTypes": media_type,
                "Recursive": "true",
                "SortOrder": "Descending",
                "Fields": fields,
            }
        else:
            sort_key = "DateCreated" if sort_type == "added" else "PremiereDate"
            params = {
                "SortBy": sort_key,
                "Limit": count,
                "IncludeItemTypes": media_type,
                "Recursive": "true",
                "SortOrder": "Descending",
                "Fields": fields,
            }

        r = requests.get(
            f"{self._cfg.base_url}/Users/{self._cfg.user_id}/Items",
            headers=headers,
            params=params,
        )
        if r.status_code != 200:
            logger.error("[Jellyfin] Items fetch failed: HTTP %s", r.status_code)
            return []

        items = r.json().get("Items", [])
        if sort_type == "random":
            return random.sample(items, min(count, len(items)))
        return items

    def _filter_items(self, items: list[dict]) -> list[dict]:
        out = []
        for item in items:
            if any(g in self._cfg.excluded_genres for g in item.get("Genres", [])):
                logger.debug("[Jellyfin] Excluded genre: %s", item["Name"])
                continue
            if any(t in self._cfg.excluded_tags for t in item.get("Tags", [])):
                logger.debug("[Jellyfin] Excluded tag: %s", item["Name"])
                continue
            if any(p in item.get("Path", "") for p in self._excluded_paths):
                logger.debug("[Jellyfin] Excluded library: %s", item["Name"])
                continue
            out.append(item)
        return out

    @staticmethod
    def _dedup(items: list[dict], seen: set[str]) -> list[dict]:
        unique = []
        for item in items:
            if item["Id"] not in seen:
                seen.add(item["Id"])
                unique.append(item)
        return unique

    def _get_mixed(
        self, limit: int, media_type: str, seen: set[str] | None = None
    ) -> list[tuple[dict, str]]:
        if seen is None:
            seen = set()
        adjusted  = int(math.ceil(limit / 3.0) * 3)
        per_group = adjusted // 3
        groups: dict[str, list[dict]] = {"aired": [], "added": [], "random": []}

        for group in ["aired", "added", "random"]:
            collected: list[dict] = []
            overfetch = 1.5
            for attempt in range(3):
                fetched   = self._fetch_items(group, int(per_group * overfetch), media_type)
                filtered  = self._filter_items(fetched)
                unique    = self._dedup(filtered, seen)
                new_items = [i for i in unique if i not in collected]
                collected.extend(new_items)
                overfetch *= 0.9
                if not new_items:
                    break
            groups[group] = collected[:per_group]

        combined: list[tuple[dict, str]] = []
        for group in ["aired", "added", "random"]:
            combined.extend((item, group) for item in groups[group])
        return combined

    def _get_season_count(self, item_id: str) -> int:
        try:
            r = requests.get(
                f"{self._cfg.base_url}/Shows/{item_id}/Seasons",
                params={"api_key": self._cfg.token},
                timeout=10,
            )
            if r.status_code == 200:
                return sum(
                    1 for s in r.json().get("Items", [])
                    if s.get("Type") == "Season" and s.get("IndexNumber", 0) > 0
                )
        except Exception:  # noqa: BLE001
            logger.debug("[Jellyfin] Season count failed for item %s", item_id, exc_info=True)
        return 0

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_item(self, item: dict, media_type: str, group_type: str) -> None:
        base_url = f"{self._cfg.base_url}/Items/{item['Id']}/Images"
        token    = self._cfg.token

        # Music albums often have no wide backdrop — fall back to cover art (Primary)
        backdrop = None
        for img_type in (["Backdrop", "Primary"] if media_type == "MusicAlbum" else ["Backdrop"]):
            url = f"{base_url}/{img_type}?api_key={token}"
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    backdrop = Image.open(BytesIO(r.content))
                    break
            except Exception:  # noqa: BLE001
                pass

        if backdrop is None:
            logger.error("[Jellyfin] No backdrop for '%s' — skipping", item["Name"])
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(backdrop, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(55)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        # Info line
        year    = (item.get("PremiereDate") or "")[:4]
        genres  = ", ".join(item.get("Genres", [])[:self._cfg.max_genres])
        cr      = item.get("OfficialRating", "")
        rating  = item.get("CommunityRating")
        parts   = [p for p in [year, genres] if p]

        if media_type == "MusicAlbum":
            artist = item.get("AlbumArtist", "")
            if artist:
                parts.insert(0, artist)
            track_count = item.get("ChildCount") or 0
            if track_count:
                parts.append(f"{track_count} {'Track' if track_count == 1 else 'Tracks'}")
        elif media_type == "Movie":
            ticks = item.get("RunTimeTicks", 0) or 0
            mins  = ticks // (10 ** 7 * 60)
            if mins:
                parts.append(self._format_runtime(mins))
        else:
            n = self._get_season_count(item["Id"])
            if n:
                parts.append(f"{n} {'Season' if n == 1 else 'Seasons'}")

        if cr:
            parts.append(cr)
        if rating:
            parts.append(f"IMDb: {rating:.1f}")

        info_text = "  \u2022  ".join(parts)
        info_pos  = (_TEXT_LEFT, _INFO_Y)
        sum_pos   = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        # Summary
        summary = self._truncate(item.get("Overview", ""))
        wrapped = "\n".join(self._wrap(summary, ft_over, draw))
        self._draw_shadow_text(
            draw, sum_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Custom label + service logo
        label_map = {
            "added": self._cfg.added_label,
            "aired": self._cfg.aired_label,
            "random": self._cfg.random_label,
        }
        label = label_map.get(group_type, self._cfg.default_label)

        bbox    = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y  = sum_pos[1] + (bbox[3] - bbox[1]) + 30
        cust_x  = 210
        self._draw_shadow_text(
            draw, (cust_x, cust_y), label, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        if self._service_logo is not None:
            lbl_bbox = draw.textbbox((0, 0), label, font=ft_cust)
            lbl_w    = lbl_bbox[2] - lbl_bbox[0]
            asc, des = ft_cust.getmetrics()
            lx = cust_x + lbl_w + 20 + self._cfg.logo_h_offset
            ly = cust_y + ((asc + des) - self._service_logo.height) // 2 + self._cfg.logo_v_offset
            canvas.paste(self._service_logo, (lx, ly), self._service_logo)

        # Media logo or title fallback
        # Music albums don't have clear logo images — go straight to text title
        logo_drawn = False
        if media_type != "MusicAlbum":
            logo_url = f"{self._cfg.base_url}/Items/{item['Id']}/Images/Logo?api_key={self._cfg.token}"
            try:
                lr = requests.get(logo_url, timeout=10)
                if lr.status_code == 200:
                    media_logo = Image.open(BytesIO(lr.content))
                    resized    = ImageUtils.resize_logo(media_logo, 1300, 400).convert("RGBA")
                    canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
                    logo_drawn = True
            except Exception:  # noqa: BLE001
                logger.debug("[Jellyfin] Logo fetch failed for '%s'", item["Name"], exc_info=True)

        if not logo_drawn:
            title_t = ImageUtils.truncate_summary(item["Name"], 30)
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), title_t, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        safe  = ImageUtils.clean_filename(item["Name"])
        year2 = item.get("ProductionYear", "")
        fname = self._save_canvas(canvas, self._staging_dir, f"{safe}_{year2}")
        logger.info("[Jellyfin] Saved: %s", fname)
        self._saved += 1

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def _run_type(self, media_type: str) -> None:
        if self._cfg.order_by == "mix":
            items = self._get_mixed(self._cfg.limit, media_type)
            for item, group_type in items:
                self._render_item(item, media_type, group_type)
                time.sleep(self._cfg.api_delay)
        else:
            fetched  = self._fetch_items(self._cfg.order_by, self._cfg.limit, media_type)
            filtered = self._filter_items(fetched)
            for item in filtered:
                self._render_item(item, media_type, self._cfg.order_by)
                time.sleep(self._cfg.api_delay)


# ---------------------------------------------------------------------------
# Plex generator
# ---------------------------------------------------------------------------

class PlexGenerator(BaseGenerator):
    """Generate backgrounds from a local Plex media server."""

    def __init__(
        self,
        cfg: PlexConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._plex = None           # lazy-initialised PlexServer
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            from plexapi.server import PlexServer  # type: ignore[import]
        except ImportError:
            logger.error("[Plex] plexapi not installed — run: pip install plexapi")
            return

        logger.info("[Plex] Connecting …")
        try:
            self._plex = PlexServer(self._cfg.base_url, self._cfg.token)
            logger.info("[Plex] Connected. Server version: %s", self._plex.version)
        except Exception as exc:  # noqa: BLE001
            logger.error("[Plex] Connection failed: %s", exc, exc_info=True)
            return

        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            if self._cfg.order_by == "mix":
                if self._cfg.download_movies:
                    self._run_mix("movie")
                if self._cfg.download_series:
                    self._run_mix("show")
                if self._cfg.download_music:
                    self._run_mix("album")
            else:
                if self._cfg.download_movies:
                    self._run_ordered("movie")
                if self._cfg.download_series:
                    self._run_ordered("show")
                if self._cfg.download_music:
                    self._run_ordered("album")
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("Plex")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        fname = "plexlogo_color.png" if self._cfg.logo_variant == "color" else "plexlogo.png"
        try:
            return Image.open(os.path.join(self._source_dir, fname)).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.warning("[Plex] Logo file not found: %s", fname)
            return None

    def _filter_plex_items(self, items: list) -> list:
        """Filter out items that match excluded genres or tags."""
        out = []
        for item in items:
            genre_tags = [g.tag for g in getattr(item, "genres", [])]
            label_tags = [la.tag for la in getattr(item, "labels", [])]
            if any(g in self._cfg.excluded_genres for g in genre_tags):
                logger.debug("[Plex] Excluded genre: %s", item.title)
                self._skipped += 1
                continue
            if any(t in self._cfg.excluded_tags for t in label_tags):
                logger.debug("[Plex] Excluded tag: %s", item.title)
                self._skipped += 1
                continue
            out.append(item)
        return out

    def _fetch_items(
        self, media_type: str, sort_type: str, count: int
    ) -> list:
        sections = [
            s for s in self._plex.library.sections()
            if s.type == media_type and s.title not in self._cfg.excluded_libraries
        ]
        items: list = []
        for sec in sections:
            items.extend(sec.search())

        if sort_type == "aired":
            items = sorted(
                [i for i in items if getattr(i, "originallyAvailableAt", None)],
                key=lambda x: x.originallyAvailableAt,
                reverse=True,
            )
        elif sort_type == "added":
            items = sorted(
                [i for i in items if getattr(i, "addedAt", None)],
                key=lambda x: x.addedAt,
                reverse=True,
            )
        elif sort_type == "random":
            return random.sample(items, min(count, len(items)))
        return items[:count]

    @staticmethod
    def _dedup(items: list, seen: set) -> list:
        unique = []
        for item in items:
            if item.ratingKey not in seen:
                seen.add(item.ratingKey)
                unique.append(item)
        return unique

    def _get_mixed(
        self, media_type: str, seen: set | None = None
    ) -> list[tuple]:
        if seen is None:
            seen = set()
        adjusted  = int(math.ceil(self._cfg.limit / 3.0) * 3)
        per_group = adjusted // 3
        groups: dict[str, list] = {"aired": [], "added": [], "random": []}

        for group in ["aired", "added", "random"]:
            collected: list = []
            overfetch = 1.5
            for _ in range(3):
                fetched   = self._fetch_items(media_type, group, int(per_group * overfetch))
                unique    = self._dedup(fetched, seen)
                new_items = [i for i in unique if i not in collected]
                collected.extend(new_items)
                overfetch *= 0.9
                if not new_items:
                    break
            groups[group] = collected[:per_group]

        combined: list[tuple] = []
        for group in ["aired", "added", "random"]:
            combined.extend((item, group) for item in groups[group])
        return combined

    def _download_media_logo(self, item) -> Image.Image | None:
        url = (
            f"{self._cfg.base_url}/library/metadata/{item.ratingKey}"
            f"/clearLogo?X-Plex-Token={self._cfg.token}"
        )
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return Image.open(BytesIO(r.content))
            logger.debug("[Plex] Logo download HTTP %s for key %s", r.status_code, item.ratingKey)
        except Exception:  # noqa: BLE001
            logger.debug("[Plex] Logo download failed for key %s", item.ratingKey, exc_info=True)
        return None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_item(self, item, media_type: str, group_type: str) -> None:
        # Albums may not have a wide art backdrop — fall back to thumb (cover art)
        backdrop = None
        for url_attr in (["artUrl", "thumbUrl"] if media_type == "album" else ["artUrl"]):
            raw_url = getattr(item, url_attr, None)
            if not raw_url:
                continue
            try:
                r = requests.get(raw_url, timeout=10)
                if r.status_code == 200:
                    backdrop = Image.open(BytesIO(r.content))
                    break
            except Exception:  # noqa: BLE001
                pass

        if backdrop is None:
            logger.error("[Plex] No backdrop for '%s' — skipping", item.title)
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(backdrop, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(55)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        # Info line
        genres = [g.tag for g in item.genres][: self._cfg.max_genres]
        rating = getattr(item, "audienceRating", None) or getattr(item, "rating", None) or ""
        parts  = [str(item.year)] + genres

        if media_type == "album":
            artist = getattr(item, "parentTitle", "") or ""
            if artist:
                parts.insert(0, artist)
            track_count = getattr(item, "leafCount", 0) or 0
            if track_count:
                parts.append(f"{track_count} {'Track' if track_count == 1 else 'Tracks'}")
        elif media_type == "movie":
            dur = getattr(item, "duration", None)
            if dur:
                parts.append(f"{dur // 3600000}h {(dur // 60000) % 60}min")
            cr = getattr(item, "contentRating", "") or ""
            if cr:
                parts.append(cr)
        else:
            try:
                n = len(item.seasons())
            except Exception:  # noqa: BLE001
                logger.debug("[Plex] Season count failed for '%s'", item.title, exc_info=True)
                n = 0
            if n:
                parts.append(f"{n} {'Season' if n == 1 else 'Seasons'}")
            cr = getattr(item, "contentRating", "") or ""
            if cr:
                parts.append(cr)

        if rating:
            parts.append(f"IMDb: {rating}")

        info_text = "  \u2022  ".join(parts)
        info_pos  = (_TEXT_LEFT, _INFO_Y)
        sum_pos   = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        # Summary
        summary = self._truncate(item.summary or "")
        wrapped = "\n".join(self._wrap(summary, ft_over, draw))
        self._draw_shadow_text(
            draw, sum_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Custom label + Plex logo
        label_map = {
            "added":  self._cfg.added_label,
            "aired":  self._cfg.aired_label,
            "random": self._cfg.random_label,
        }
        label  = label_map.get(group_type, self._cfg.default_label)
        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = sum_pos[1] + (bbox[3] - bbox[1]) + 30
        cust_x = 210
        self._draw_shadow_text(
            draw, (cust_x, cust_y), label, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        if self._service_logo is not None:
            lbl_bbox = draw.textbbox((0, 0), label, font=ft_cust)
            lbl_w    = lbl_bbox[2] - lbl_bbox[0]
            asc, des = ft_cust.getmetrics()
            lx = cust_x + lbl_w + 20 + self._cfg.logo_h_offset
            ly = cust_y + ((asc + des) - self._service_logo.height) // 2 + self._cfg.logo_v_offset
            canvas.paste(self._service_logo, (lx, ly), self._service_logo)

        # Media logo or title fallback
        # Music albums don't have clear logo images — go straight to text title
        media_logo = None if media_type == "album" else self._download_media_logo(item)
        if media_logo:
            resized = ImageUtils.resize_logo(media_logo, 1300, 400).convert("RGBA")
            canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
        else:
            title_t = ImageUtils.truncate_summary(item.title, 30)
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), title_t, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        safe  = ImageUtils.clean_filename(item.title)
        fname = self._save_canvas(canvas, self._staging_dir, safe)
        logger.info("[Plex] Saved: %s", fname)
        self._saved += 1

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------

    def _run_mix(self, media_type: str) -> None:
        seen  = set()
        items = self._get_mixed(media_type, seen)
        for item, group_type in items:
            filtered = self._filter_plex_items([item])
            if filtered:
                self._render_item(filtered[0], media_type, group_type)
            time.sleep(self._cfg.api_delay)

    def _run_ordered(self, media_type: str) -> None:
        items    = self._fetch_items(media_type, self._cfg.order_by, self._cfg.limit)
        filtered = self._filter_plex_items(items)
        for item in filtered:
            self._render_item(item, media_type, self._cfg.order_by)
            time.sleep(self._cfg.api_delay)


# ---------------------------------------------------------------------------
# Plex-friend generator
# ---------------------------------------------------------------------------

class PlexFriendGenerator(BaseGenerator):
    """Generate backgrounds from Plex friends' shared libraries."""

    def __init__(
        self,
        cfg: PlexFriendConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            from plexapi.myplex import MyPlexAccount  # type: ignore[import]
        except ImportError:
            logger.error("[PlexFriend] plexapi not installed — run: pip install plexapi")
            return

        if not self._cfg.token:
            logger.error("[PlexFriend] PLEX_TOKEN not set — skipping")
            return

        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)

        logger.info("[PlexFriend] Discovering friend servers …")
        try:
            account    = MyPlexAccount(token=self._cfg.token)
            friend_map = {u.id: u.title for u in account.users()}
            servers    = {}
            for res in account.resources():
                if res.provides != "server" or res.owned:
                    continue
                owner = friend_map.get(res.ownerId)
                if not owner:
                    continue
                if self._cfg.target_friend and owner != self._cfg.target_friend:
                    continue
                try:
                    plex = res.connect()
                    servers[owner] = plex
                    logger.info("[PlexFriend] Connected to %s's server: %s", owner, res.name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[PlexFriend] Could not connect to %s: %s", owner, exc, exc_info=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("[PlexFriend] Account access failed: %s", exc, exc_info=True)
            self._abort_output(staging)
            return

        try:
            for friend, plex in servers.items():
                logger.info("[PlexFriend] Processing %s's library …", friend)
                self._process_friend(plex, friend)
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("PlexFriend")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        fname = "plexlogo_color.png" if self._cfg.logo_variant == "color" else "plexlogo.png"
        try:
            return Image.open(os.path.join(self._source_dir, fname)).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None

    def _process_friend(self, plex, friend: str) -> None:
        if self._cfg.download_movies:
            items = self._sorted_items(plex, "movie")
            for item in items[:self._cfg.limit]:
                self._render_item(item, "movie", self._cfg.order_by, plex, friend)
                time.sleep(self._cfg.api_delay)
        if self._cfg.download_series:
            items = self._sorted_items(plex, "show")
            for item in items[:self._cfg.limit]:
                self._render_item(item, "show", self._cfg.order_by, plex, friend)
                time.sleep(self._cfg.api_delay)

    def _sorted_items(self, plex, media_type: str) -> list:
        items = plex.library.search(libtype=media_type)
        key   = "originallyAvailableAt" if self._cfg.order_by == "aired" else "addedAt"
        return sorted(
            [i for i in items if getattr(i, key, None)],
            key=lambda x: getattr(x, key),
            reverse=True,
        )

    def _render_item(
        self, item, media_type: str, order_type: str, plex, friend: str
    ) -> None:
        art_url = item.artUrl
        if not art_url:
            self._skipped += 1
            return
        try:
            r = requests.get(art_url, timeout=10)
            r.raise_for_status()
            backdrop = Image.open(BytesIO(r.content))
        except Exception as exc:  # noqa: BLE001
            logger.error("[PlexFriend] Art error for '%s': %s", item.title, exc, exc_info=True)
            self._errors += 1
            return

        canvas = self._renderer.build_canvas(backdrop, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(55)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        # Info
        genres = [g.tag for g in item.genres][: self._cfg.max_genres]
        rating = getattr(item, "audienceRating", None) or getattr(item, "rating", None) or ""
        parts  = [str(item.year)] + genres

        if media_type == "movie":
            dur = getattr(item, "duration", None)
            if dur:
                parts.append(f"{dur // 3600000}h {(dur // 60000) % 60}min")
        else:
            try:
                n = len(item.seasons())
                if n:
                    parts.append(f"{n} {'Season' if n == 1 else 'Seasons'}")
            except Exception:  # noqa: BLE001
                logger.debug("[PlexFriend] Season count failed for '%s'", item.title, exc_info=True)

        if rating:
            parts.append(f"IMDb: {rating}")

        info_text = "  \u2022  ".join(parts)
        info_pos  = (_TEXT_LEFT, _INFO_Y)
        sum_pos   = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        summary = self._truncate(item.summary or "")
        wrapped = "\n".join(self._wrap(summary, ft_over, draw))
        self._draw_shadow_text(
            draw, sum_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Label with friend's name
        if order_type == "added":
            label = f"{self._cfg.added_label} {friend}'s"
        elif order_type == "aired":
            label = self._cfg.aired_label
        elif order_type == "random":
            label = f"{self._cfg.random_label} {friend}'s"
        else:
            label = f"{self._cfg.default_label} {friend}'s"

        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = sum_pos[1] + (bbox[3] - bbox[1]) + 30
        cust_x = 210
        self._draw_shadow_text(
            draw, (cust_x, cust_y), label, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        if self._service_logo is not None:
            lbl_bbox = draw.textbbox((0, 0), label, font=ft_cust)
            lbl_w    = lbl_bbox[2] - lbl_bbox[0]
            asc, des = ft_cust.getmetrics()
            lx = cust_x + lbl_w + 20 + self._cfg.logo_h_offset
            ly = cust_y + ((asc + des) - self._service_logo.height) // 2 + self._cfg.logo_v_offset
            canvas.paste(self._service_logo, (lx, ly), self._service_logo)

        # Media logo or title fallback
        logo_url = (
            f"{plex._baseurl}/library/metadata/{item.ratingKey}"
            f"/clearLogo?X-Plex-Token={plex._token}"
        )
        logo_drawn = False
        try:
            lr = requests.get(logo_url, timeout=10)
            if lr.status_code == 200:
                ml      = Image.open(BytesIO(lr.content))
                resized = ImageUtils.resize_logo(ml, 1300, 400).convert("RGBA")
                canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
                logo_drawn = True
        except Exception:  # noqa: BLE001
            logger.debug("[PlexFriend] Logo fetch failed for '%s'", item.title, exc_info=True)

        if not logo_drawn:
            title_t = ImageUtils.truncate_summary(item.title, 30)
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), title_t, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        safe  = ImageUtils.clean_filename(item.title)
        fname = self._save_canvas(canvas, self._staging_dir, safe)
        logger.info("[PlexFriend] Saved: %s", fname)
        self._saved += 1


# ---------------------------------------------------------------------------
# Radarr / Sonarr generator
# ---------------------------------------------------------------------------

class RadarrSonarrGenerator(BaseGenerator):
    """Generate backgrounds for upcoming releases from Radarr and Sonarr."""

    def __init__(
        self,
        cfg: RadarrSonarrConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg     = cfg
        self._headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {cfg.tmdb_bearer_token}",
        }
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("[RadarrSonarr] Starting …")
        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            entries: list[tuple[int, bool]] = []
            if self._cfg.sonarr_api_key:
                entries.extend(self._get_sonarr_upcoming())
            if self._cfg.radarr_api_key:
                entries.extend(self._get_radarr_upcoming())

            seen_ids: set[tuple[int, bool]] = set()
            for tmdb_id, is_movie in entries:
                key = (tmdb_id, is_movie)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                self._render_tmdb_item(tmdb_id, is_movie)
                time.sleep(self._cfg.api_delay)
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("RadarrSonarr")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str, headers: dict | None = None, params: dict | None = None) -> Any:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("[RadarrSonarr] Fetch error %s: %s", url, exc, exc_info=True)
            return {} if "calendar" not in url else []

    def _get_radarr_upcoming(self) -> list[tuple[int, bool]]:
        start = datetime.now(timezone.utc).date()
        end   = start + timedelta(days=self._cfg.days_ahead)
        hdrs  = {"X-Api-Key": self._cfg.radarr_api_key}
        movies = self._fetch_json(f"{self._cfg.radarr_url}/api/v3/movie", headers=hdrs)
        if not isinstance(movies, list):
            return []

        def _parse(d: str) -> datetime.date | None:
            if not d:
                return None
            try:
                return datetime.strptime(d, "%Y-%m-%dT%H:%M:%SZ").date()
            except Exception:  # noqa: BLE001
                logger.debug("[RadarrSonarr] Date parse error: %r", d, exc_info=True)
                return None

        entries = []
        for m in movies:
            if not m.get("monitored") or m.get("hasFile"):
                continue
            d_dt = _parse(m.get("digitalRelease", ""))
            p_dt = _parse(m.get("physicalRelease", ""))
            if (d_dt and start <= d_dt <= end) or (p_dt and start <= p_dt <= end):
                if m.get("tmdbId"):
                    entries.append((m["tmdbId"], True))
        return entries

    def _get_sonarr_upcoming(self) -> list[tuple[int, bool]]:
        start = datetime.now(timezone.utc).date()
        end   = start + timedelta(days=self._cfg.days_ahead)
        url   = f"{self._cfg.sonarr_url}/api/v3/calendar?start={start}&end={end}"
        hdrs  = {"X-Api-Key": self._cfg.sonarr_api_key}
        eps   = self._fetch_json(url, headers=hdrs)
        if not isinstance(eps, list):
            return []

        entries: set[tuple[int, bool]] = set()
        for ep in eps:
            if not ep.get("monitored"):
                continue
            series_id = ep.get("seriesId")
            if not series_id:
                continue
            series = self._fetch_json(
                f"{self._cfg.sonarr_url}/api/v3/series/{series_id}", headers=hdrs
            )
            if not series.get("monitored"):
                continue
            tvdb_id = series.get("tvdbId")
            if tvdb_id:
                client   = TMDBClient(
                    TMDBConfig(
                        bearer_token=self._cfg.tmdb_bearer_token,
                        base_url=self._cfg.tmdb_base_url,
                        language=self._cfg.language,
                    )
                )
                tmdb_id = client.find_by_tvdb(tvdb_id)
                if tmdb_id:
                    entries.add((tmdb_id, False))
        return list(entries)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        path = os.path.join(self._source_dir, self._cfg.logo_filename)
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.debug("[RadarrSonarr] Logo not found: %s", self._cfg.logo_filename, exc_info=True)
            return None

    def _render_tmdb_item(self, tmdb_id: int, is_movie: bool) -> None:
        client = TMDBClient(
            TMDBConfig(
                bearer_token=self._cfg.tmdb_bearer_token,
                base_url=self._cfg.tmdb_base_url,
                img_base=self._cfg.tmdb_img_base,
                language=self._cfg.language,
            )
        )
        mt   = "movie" if is_movie else "tv"
        data = self._fetch_json(
            f"{self._cfg.tmdb_base_url}/{mt}/{tmdb_id}?language={self._cfg.language}",
            headers=self._headers,
        )
        if not data:
            self._skipped += 1
            return

        title    = data.get("title") or data.get("name") or "Unknown"

        # Genre filtering
        genres_list = [g["name"] for g in data.get("genres", [])]
        if self._cfg.excluded_genres and any(
            g in self._cfg.excluded_genres for g in genres_list
        ):
            logger.debug("[RadarrSonarr] Excluded genre for '%s' — skipping", title)
            self._skipped += 1
            return

        # Vote average filtering
        vote_avg = data.get("vote_average") or 0.0
        if vote_avg < self._cfg.min_vote_average:
            logger.debug(
                "[RadarrSonarr] vote_average %.1f < %.1f for '%s' — skipping",
                vote_avg, self._cfg.min_vote_average, title,
            )
            self._skipped += 1
            return

        # Keyword filtering
        if self._cfg.excluded_keywords:
            if is_movie:
                kws = client.movie_keywords(tmdb_id)
            else:
                kws = client.tv_keywords(tmdb_id)
            if any(kw in kws for kw in self._cfg.excluded_keywords):
                logger.debug("[RadarrSonarr] Excluded keyword for '%s' — skipping", title)
                self._skipped += 1
                return

        backdrop = data.get("backdrop_path")
        if not backdrop:
            logger.warning("[RadarrSonarr] No backdrop for '%s' — skipping", title)
            self._skipped += 1
            return

        img = self._fetch_image(f"{self._cfg.tmdb_img_base}{backdrop}")
        if img is None:
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(img, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(50)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        genre    = ", ".join(genres_list[: self._cfg.max_genres])
        year     = (data.get("release_date") or data.get("first_air_date", ""))[:4]
        rating   = data.get("vote_average")
        overview = data.get("overview", "")

        if is_movie:
            runtime = self._format_runtime(data.get("runtime", 0))
        else:
            n       = data.get("number_of_seasons", 0)
            runtime = f"{n} {'Season' if n == 1 else 'Seasons'}"

        rating_t  = f"TMDB: {rating:.1f}" if rating else "TMDB: N/A"
        info_text = "  \u2022  ".join(p for p in [genre, year, runtime, rating_t] if p)

        info_pos  = (_TEXT_LEFT, _INFO_Y)
        sum_pos   = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        ov_t    = self._truncate(overview)
        wrapped = "\n".join(self._wrap(ov_t, ft_over, draw))
        self._draw_shadow_text(
            draw, sum_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Dynamic custom text position
        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = sum_pos[1] + (bbox[3] - bbox[1]) + 30
        custom_text = self._cfg.movie_custom_text if is_movie else self._cfg.tv_custom_text
        self._draw_shadow_text(
            draw, (210, cust_y), custom_text, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        # Service logo at fixed position
        if self._service_logo is not None:
            logo_pos = (970, 890) if is_movie else (1010, 890)
            canvas.paste(self._service_logo, logo_pos, self._service_logo)

        # Media logo or title fallback
        logo_fp   = client.get_logo(mt, tmdb_id)
        logo_drawn = False
        if logo_fp:
            logo_img = self._fetch_image(f"{self._cfg.tmdb_img_base}{logo_fp}")
            if logo_img:
                resized = ImageUtils.resize_logo(logo_img, 1000, 500).convert("RGBA")
                canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
                logo_drawn = True

        if not logo_drawn:
            t = ImageUtils.truncate_summary(title, 45)
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), t, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        fname = self._save_canvas(canvas, self._staging_dir, ImageUtils.clean_filename(title))
        logger.info("[RadarrSonarr] Saved: %s", fname)
        self._saved += 1


# ---------------------------------------------------------------------------
# Trakt generator
# ---------------------------------------------------------------------------

class TraktGenerator(BaseGenerator):
    """Generate backgrounds for items in a Trakt user list."""

    def __init__(
        self,
        cfg: TraktConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg     = cfg
        self._headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {cfg.tmdb_bearer_token}",
        }
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("[Trakt] Fetching list '%s' for @%s …", self._cfg.list_name, self._cfg.username)
        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            movies, shows = self._fetch_trakt_list()
            logger.info("[Trakt] Found %d movies and %d shows", len(movies), len(shows))

            # Apply download_movies / download_series filter
            if not self._cfg.download_movies:
                movies = []
            if not self._cfg.download_series:
                shows = []

            # Apply per-type limit (0 = no limit)
            if self._cfg.limit:
                movies = movies[: self._cfg.limit]
                shows  = shows[: self._cfg.limit]

            for title, tmdb_id in shows + movies:
                if not tmdb_id:
                    continue
                is_movie   = (title, tmdb_id) in [(t, i) for t, i in movies]
                media_type = "movie" if is_movie else "tv"
                self._render_trakt_item(title, tmdb_id, media_type)
                time.sleep(self._cfg.api_delay)
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("Trakt")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        path = os.path.join(self._source_dir, "traktlogo.png")
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.debug("[Trakt] Logo not found: %s", path, exc_info=True)
            return None

    def _fetch_trakt_list(self) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
        url  = f"https://api.trakt.tv/users/{self._cfg.username}/lists/{self._cfg.list_name}/items"
        hdrs = {
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": self._cfg.api_key,
        }
        try:
            r = requests.get(url, headers=hdrs, timeout=10)
            if r.status_code != 200:
                logger.error("[Trakt] List fetch failed: HTTP %s", r.status_code)
                return [], []
            items   = r.json()
            movies  = [
                (it["movie"]["title"], it["movie"]["ids"]["tmdb"])
                for it in items
                if it["type"] == "movie"
            ]
            shows   = [
                (it["show"]["title"], it["show"]["ids"]["tmdb"])
                for it in items
                if it["type"] == "show"
            ]
            return movies, shows
        except Exception as exc:  # noqa: BLE001
            logger.error("[Trakt] List fetch error: %s", exc, exc_info=True)
            return [], []

    def _render_trakt_item(
        self, title: str, tmdb_id: int, media_type: str
    ) -> None:
        mt   = "movie" if media_type == "movie" else "tv"
        url  = f"{self._cfg.tmdb_base_url}/{mt}/{tmdb_id}?language=en-US"
        try:
            r = requests.get(url, headers=self._headers, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("[Trakt] TMDB details error for '%s': %s", title, exc, exc_info=True)
            self._errors += 1
            return

        backdrop = data.get("backdrop_path")
        if not backdrop:
            logger.warning("[Trakt] No backdrop for '%s' — skipping", title)
            self._skipped += 1
            return

        img = self._fetch_image(f"{self._cfg.tmdb_img_base}{backdrop}")
        if img is None:
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(img, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(50)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        genres_list = data.get("genres", [])
        genres  = ", ".join(g["name"] for g in genres_list[: self._cfg.max_genres])
        year    = (data.get("release_date") or data.get("first_air_date", ""))[:4]
        rating  = round(data.get("vote_average", 0), 1)
        overview = data.get("overview", "")

        if media_type == "movie":
            dur = data.get("runtime", 0)
            h, m = divmod(dur, 60)
            runtime = f"{h}h{m}min"
        else:
            seasons = data.get("number_of_seasons", 0)
            runtime = f"{seasons} {'Season' if seasons == 1 else 'Seasons'}"

        info_text = f"{genres}  \u2022  {year}  \u2022  {runtime}  \u2022  TMDB: {rating}"

        info_pos  = (_TEXT_LEFT, _INFO_Y)
        sum_pos   = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        ov_t    = self._truncate(overview)
        wrapped = "\n".join(self._wrap(ov_t, ft_over, draw))
        self._draw_shadow_text(
            draw, sum_pos, wrapped, ft_over,
            self._color(self._cfg.summary_color_override, "summary_color"),
        )

        # Custom label (use custom_text override or derive from list_name)
        custom_text = self._cfg.custom_text or f"Now on my {self._cfg.list_name} "
        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = sum_pos[1] + (bbox[3] - bbox[1]) + 30
        self._draw_shadow_text(
            draw, (210, cust_y), custom_text, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        # Trakt service logo beside custom text
        if self._service_logo is not None:
            lbl_w = draw.textbbox((0, 0), custom_text, font=ft_cust)[2]
            canvas.paste(self._service_logo, (210 + lbl_w + 10, cust_y), self._service_logo)

        # TMDB logo or title fallback
        logo_url  = f"{self._cfg.tmdb_base_url}/{mt}/{tmdb_id}/images?language=en"
        logo_path = None
        try:
            lr = requests.get(logo_url, headers=self._headers, timeout=10)
            if lr.status_code == 200:
                logos = lr.json().get("logos", [])
                en    = [lo for lo in logos if lo.get("iso_639_1") == "en" and lo["file_path"].endswith(".png")]
                if en:
                    logo_path = en[0]["file_path"]
        except Exception:  # noqa: BLE001
            logger.debug("[Trakt] Logo image fetch failed for '%s'", title, exc_info=True)

        logo_drawn = False
        if logo_path:
            logo_img = self._fetch_image(f"{self._cfg.tmdb_img_base}{logo_path}")
            if logo_img:
                resized = ImageUtils.resize_logo(logo_img, 1000, 500).convert("RGBA")
                canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
                logo_drawn = True

        if not logo_drawn:
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), title, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        fname = self._save_canvas(canvas, self._staging_dir, ImageUtils.clean_filename(title))
        logger.info("[Trakt] Saved: %s", fname)
        self._saved += 1


# ---------------------------------------------------------------------------
# Lidarr generator
# ---------------------------------------------------------------------------

class LidarrGenerator(BaseGenerator):
    """Generate backgrounds for upcoming album releases from Lidarr."""

    def __init__(
        self,
        cfg: LidarrConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._headers = {"X-Api-Key": cfg.api_key, "accept": "application/json"}
        self._service_logo: Image.Image | None = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("[Lidarr] Starting …")
        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            albums = self._get_upcoming_albums()
            seen_ids: set[int] = set()
            for album in albums:
                album_id = album.get("id")
                if album_id in seen_ids:
                    continue
                seen_ids.add(album_id)
                self._render_album(album)
                time.sleep(self._cfg.api_delay)
            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("Lidarr")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_json(self, url: str) -> Any:
        try:
            r = requests.get(url, headers=self._headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("[Lidarr] Fetch error %s: %s", url, exc, exc_info=True)
            return []

    def _get_upcoming_albums(self) -> list[dict]:
        start = datetime.now(timezone.utc).date()
        end   = start + timedelta(days=self._cfg.days_ahead)
        url   = (
            f"{self._cfg.base_url}/api/v1/calendar"
            f"?start={start}&end={end}&includeArtist=true"
        )
        result = self._fetch_json(url)
        albums = result if isinstance(result, list) else []
        logger.info("[Lidarr] Calendar returned %d album(s) for next %d days", len(albums), self._cfg.days_ahead)
        return albums

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        path = os.path.join(self._source_dir, self._cfg.logo_filename)
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.debug("[Lidarr] Logo not found: %s", path, exc_info=True)
            return None

    def _media_cover_url(self, path: str) -> str:
        """Append the API key query parameter to a MediaCover path."""
        return f"{self._cfg.base_url}/api/v1/MediaCover/{path}?apikey={self._cfg.api_key}"

    def _get_backdrop_url(self, album: dict) -> str | None:
        """Return the best available image URL for this album."""
        artist    = album.get("artist") or {}
        artist_id = artist.get("id") or album.get("artistId")
        album_id  = album.get("id")

        # Prefer artist fanart
        if artist_id:
            for img in artist.get("images", []):
                if img.get("coverType") == "fanart":
                    return self._media_cover_url(f"artist/{artist_id}/fanart.jpg")

        # Fall back to album cover art
        if album_id:
            for img in album.get("images", []):
                if img.get("coverType") == "cover":
                    return self._media_cover_url(f"album/{album_id}/cover.jpg")

        # Last resort: any artist image
        if artist_id:
            images = artist.get("images", [])
            if images:
                img_type = images[0].get("coverType", "poster")
                return self._media_cover_url(f"artist/{artist_id}/{img_type}.jpg")

        return None

    def _render_album(self, album: dict) -> None:
        title       = album.get("title") or "Unknown Album"
        artist      = album.get("artist") or {}
        artist_name = artist.get("artistName") or artist.get("name") or "Unknown Artist"

        # Metadata
        release_date = (album.get("releaseDate") or "")[:10]
        track_count  = (album.get("statistics") or {}).get("trackCount") or 0
        genres       = artist.get("genres") or []
        genre_text   = ", ".join(genres[:3]) if genres else ""

        info_parts = [p for p in [artist_name, genre_text, release_date] if p]
        if track_count:
            info_parts.append(f"{track_count} tracks")
        info_text = "  \u2022  ".join(info_parts)

        # Fetch backdrop
        img_url = self._get_backdrop_url(album)
        if img_url is None:
            logger.warning("[Lidarr] No image available for '%s' — skipping", title)
            self._skipped += 1
            return

        img = self._fetch_image(img_url)
        if img is None:
            logger.warning("[Lidarr] Could not download image for '%s' — skipping", title)
            self._skipped += 1
            return

        canvas = self._renderer.build_canvas(img, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        info_pos = (_TEXT_LEFT, _INFO_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        # Custom tagline below info line
        self._draw_shadow_text(
            draw, (_TEXT_LEFT, _SUMMARY_Y), self._cfg.custom_text, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        # Service logo at fixed position
        if self._service_logo is not None:
            canvas.paste(self._service_logo, (970, 890), self._service_logo)

        # Album title as text (no TMDB logo API for music)
        t = ImageUtils.truncate_summary(title, 45)
        self._draw_shadow_text(
            draw, (_TITLE_X, _TITLE_Y), t, ft_title,
            self._color(self._cfg.main_color_override, "main_color"),
        )

        fname = self._save_canvas(canvas, self._staging_dir, ImageUtils.clean_filename(title))
        logger.info("[Lidarr] Saved: %s", fname)
        self._saved += 1


# ---------------------------------------------------------------------------
# Steam generator
# ---------------------------------------------------------------------------

class SteamGenerator(BaseGenerator):
    """Generate backgrounds from a Steam user's game library.

    Three groups are produced in each run:

    * **Recently played** — games played in the last two weeks
      (``IPlayerService/GetRecentlyPlayedGames``).
    * **Waiting to be played** — unplayed owned games (playtime == 0).
      Steam's public Web API does not expose purchase dates, so zero-playtime
      games are the closest available approximation.
    * **Random** — a random sample from the rest of the library.
    """

    _API_BASE   = "https://api.steampowered.com"
    _CDN_BASE   = "https://cdn.akamai.steamstatic.com/steam/apps"
    _STORE_BASE = "https://store.steampowered.com/api"

    def __init__(
        self,
        cfg: SteamConfig,
        shared: SharedConfig,
        renderer: BackgroundRenderer,
        font_manager: FontManager,
        source_dir: str,
    ) -> None:
        super().__init__(shared, renderer, font_manager, source_dir)
        self._cfg = cfg
        self._service_logo: Image.Image | None = None
        self._details_cache: dict[int, dict] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("[Steam] Starting …")
        self._fonts.ensure_font()
        self._service_logo = self._load_service_logo()
        staging = self._prepare_output_dir(self._cfg.output_dir)
        try:
            recently_played = self._get_recently_played()
            owned           = self._get_owned_games()
            played_ids      = {g["appid"] for g in recently_played}
            unplayed        = self._get_recently_purchased(owned, exclude_ids=played_ids)
            unplayed_ids    = {g["appid"] for g in unplayed}
            random_games    = self._get_random_games(owned, exclude_ids=played_ids | unplayed_ids)

            seen: set[int] = set()
            for game in recently_played:
                appid = game["appid"]
                if appid not in seen:
                    seen.add(appid)
                    self._render_game(appid, game.get("name", ""), self._cfg.recently_played_label)
                    time.sleep(self._cfg.api_delay)

            for game in unplayed:
                appid = game["appid"]
                if appid not in seen:
                    seen.add(appid)
                    self._render_game(appid, game.get("name", ""), self._cfg.unplayed_label)
                    time.sleep(self._cfg.api_delay)

            for game in random_games:
                appid = game["appid"]
                if appid not in seen:
                    seen.add(appid)
                    self._render_game(appid, game.get("name", ""), self._cfg.random_label)
                    time.sleep(self._cfg.api_delay)

            self._commit_output(staging)
        except Exception:
            self._abort_output(staging)
            raise
        self._log_summary("Steam")

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    def _fetch_api(self, url: str, params: dict | None = None) -> dict:
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("[Steam] Fetch error %s: %s", url, exc, exc_info=True)
            return {}

    def _get_recently_played(self) -> list[dict]:
        data  = self._fetch_api(
            f"{self._API_BASE}/IPlayerService/GetRecentlyPlayedGames/v1",
            params={
                "key":     self._cfg.api_key,
                "steamid": self._cfg.user_id,
                "count":   self._cfg.recently_played_count * 2,
            },
        )
        games = data.get("response", {}).get("games", [])
        games.sort(key=lambda g: g.get("rtime_last_played", 0), reverse=True)
        logger.info("[Steam] Recently played: %d game(s)", len(games))
        return games[: self._cfg.recently_played_count]

    def _get_owned_games(self) -> list[dict]:
        data  = self._fetch_api(
            f"{self._API_BASE}/IPlayerService/GetOwnedGames/v1",
            params={
                "key":                  self._cfg.api_key,
                "steamid":              self._cfg.user_id,
                "include_appinfo":      1,
                "include_played_free_games": 1,
            },
        )
        games = data.get("response", {}).get("games", [])
        logger.info("[Steam] Library: %d game(s)", len(games))
        return games

    def _get_recently_purchased(
        self, owned: list[dict], exclude_ids: set[int]
    ) -> list[dict]:
        """Return a sample of unplayed owned games.

        Steam's Web API does not expose purchase dates; games with zero
        playtime are the best available proxy for recently acquired titles.
        """
        unplayed = [
            g for g in owned
            if g.get("playtime_forever", 0) == 0 and g["appid"] not in exclude_ids
        ]
        random.shuffle(unplayed)
        logger.info("[Steam] Unplayed (recently purchased proxy): %d game(s)", len(unplayed))
        return unplayed[: self._cfg.unplayed_count]

    def _get_random_games(
        self, owned: list[dict], exclude_ids: set[int]
    ) -> list[dict]:
        pool = [g for g in owned if g["appid"] not in exclude_ids]
        return random.sample(pool, min(self._cfg.random_count, len(pool)))

    def _get_game_details(self, appid: int) -> dict:
        if appid in self._details_cache:
            return self._details_cache[appid]
        try:
            r = requests.get(
                f"{self._STORE_BASE}/appdetails",
                params={"appids": appid, "cc": "us", "l": "english"},
                timeout=15,
            )
            if r.status_code == 200:
                payload = r.json().get(str(appid), {})
                if payload.get("success"):
                    details = payload.get("data", {})
                    self._details_cache[appid] = details
                    return details
            logger.debug("[Steam] Store details HTTP %s for appid %s", r.status_code, appid)
        except Exception:  # noqa: BLE001
            logger.debug("[Steam] Store details failed for appid %s", appid, exc_info=True)
        self._details_cache[appid] = {}
        return {}

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _load_service_logo(self) -> Image.Image | None:
        path = os.path.join(self._source_dir, self._cfg.logo_filename)
        try:
            return Image.open(path).convert("RGBA")
        except Exception:  # noqa: BLE001
            logger.debug("[Steam] Logo not found: %s", path, exc_info=True)
            return None

    def _render_game(self, appid: int, name: str, label: str) -> None:
        # Backdrop: wide library hero → header image fallback
        img = self._fetch_image(f"{self._CDN_BASE}/{appid}/library_hero.jpg")
        if img is None:
            img = self._fetch_image(f"{self._CDN_BASE}/{appid}/header.jpg")
        if img is None:
            logger.warning("[Steam] No image for '%s' (appid %s) — skipping", name, appid)
            self._skipped += 1
            return

        details   = self._get_game_details(appid)
        genres    = ", ".join(g["description"] for g in details.get("genres", [])[:3])
        year      = (details.get("release_date") or {}).get("date", "")
        developer = ", ".join((details.get("developers") or [])[:2])
        overview  = details.get("short_description", "")

        info_parts = [p for p in [developer, genres, year] if p]
        info_text  = "  \u2022  ".join(info_parts)

        canvas = self._renderer.build_canvas(img, self._source_dir, shared=self._shared)
        draw   = ImageDraw.Draw(canvas)

        ft_title = self._fonts.get_font(190)
        ft_info  = self._fonts.get_font(50)
        ft_over  = self._fonts.get_font(50)
        ft_cust  = self._fonts.get_font(60)

        info_pos = (_TEXT_LEFT, _INFO_Y)
        sum_pos  = (_TEXT_LEFT, _SUMMARY_Y)

        self._draw_shadow_text(
            draw, info_pos, info_text, ft_info,
            self._color(self._cfg.info_color_override, "info_color"),
        )

        wrapped = ""
        if overview:
            ov_t    = self._truncate(overview)
            wrapped = "\n".join(self._wrap(ov_t, ft_over, draw))
            self._draw_shadow_text(
                draw, sum_pos, wrapped, ft_over,
                self._color(self._cfg.summary_color_override, "summary_color"),
            )

        bbox   = draw.textbbox((0, 0), wrapped, font=ft_over)
        cust_y = sum_pos[1] + (bbox[3] - bbox[1]) + 30 if wrapped else sum_pos[1]
        cust_x = 210
        self._draw_shadow_text(
            draw, (cust_x, cust_y), label, ft_cust,
            self._color(self._cfg.metadata_color_override, "metadata_color"),
        )

        if self._service_logo is not None:
            lbl_bbox = draw.textbbox((0, 0), label, font=ft_cust)
            lbl_w    = lbl_bbox[2] - lbl_bbox[0]
            asc, des = ft_cust.getmetrics()
            lx = cust_x + lbl_w + 20
            ly = cust_y + ((asc + des) - self._service_logo.height) // 2
            canvas.paste(self._service_logo, (lx, ly), self._service_logo)

        # Game logo or title text fallback
        logo_img   = self._fetch_image(f"{self._CDN_BASE}/{appid}/logo.png")
        logo_drawn = False
        if logo_img:
            resized = ImageUtils.resize_logo(logo_img, 1000, 500).convert("RGBA")
            canvas.paste(resized, (210, info_pos[1] - resized.height - 25), resized)
            logo_drawn = True

        if not logo_drawn:
            t = ImageUtils.truncate_summary(name, 45)
            self._draw_shadow_text(
                draw, (_TITLE_X, _TITLE_Y), t, ft_title,
                self._color(self._cfg.main_color_override, "main_color"),
            )

        fname = self._save_canvas(canvas, self._staging_dir, ImageUtils.clean_filename(name))
        logger.info("[Steam] Saved: %s", fname)
        self._saved += 1


# ---------------------------------------------------------------------------
# Source-file validation
# ---------------------------------------------------------------------------


def validate_source_files(source_dir: str, shared: "SharedConfig") -> None:
    """Raise ``FileNotFoundError`` if overlay-style required files are missing."""
    if shared.background_style != "overlay":
        return
    for fname in ("bckg.png", "overlay.png"):
        path = os.path.join(source_dir, fname)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Overlay style requires {fname!r} at {path!r}. "
                "Set BACKGROUND_STYLE=color or add the missing file."
            )


# ---------------------------------------------------------------------------
# Public YAML helpers  (mirror of _load_yaml / _apply_section in main.py)
# ---------------------------------------------------------------------------


def load_yaml(path: str) -> dict:
    """Load *path* as YAML; return ``{}`` on file-not-found or parse error."""
    import yaml as _yaml  # local import keeps the top-level import list clean
    try:
        with open(path, encoding="utf-8") as fh:
            return _yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except _yaml.YAMLError:
        return {}


def apply_config(obj: object, yaml_cfg: dict, section: str) -> None:
    """Overlay a named YAML section onto a config dataclass in-place."""
    for key, val in yaml_cfg.get(section, {}).items():
        if hasattr(obj, key):
            setattr(obj, key, tuple(val) if isinstance(val, list) else val)


# ---------------------------------------------------------------------------
# Logging setup  (called once from main.py)
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str = "logs") -> None:
    """Configure file + console logging for the application.

    * **File handler** — ``<log_dir>/androidtvbg.log``, DEBUG level,
      rotating at 5 MB with 5 backups kept.  Full tracebacks are written
      here so failures can be diagnosed after the fact.
    * **Console handler** — INFO level, mirrors the existing stdout output.

    Call this once from ``main.py`` before running any generators.

    Args:
        log_dir: Directory for the rotating log file.
                 Created automatically when absent.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "androidtvbg.log")

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler — full DEBUG output with tracebacks
    fh = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console handler — INFO and above (matches previous print() behaviour)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.setLevel(logging.DEBUG)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("Logging started — file: %s", os.path.abspath(log_path))


# ---------------------------------------------------------------------------
# Factory helpers  (used by main.py)
# ---------------------------------------------------------------------------

def make_renderer(shared: SharedConfig) -> BackgroundRenderer:
    """Return the correct renderer for the configured *background_style*."""
    if shared.background_style == "overlay":
        return OverlayRenderer()
    return ColorRenderer()


def make_font_manager(shared: SharedConfig) -> FontManager:
    """Return a ``FontManager`` initialised with the shared font settings."""
    return FontManager(user_url=shared.font_url, user_name=shared.font_name)
