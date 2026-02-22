# Android TV Background

Automatically generates 4 K Android TV wallpapers from your media services.
Each background shows the backdrop image, title logo (or text fallback),
plot summary, metadata, and a custom tagline — ready to be picked up by
your TV launcher.

![y](https://github.com/adelatour11/androidtvbackground/assets/1473994/8039b728-469f-4fd9-8ca5-920e57bd16d9)


The scripts retrieves the background of the latest media (movies, tv shows, music and games), resizes the image, add an overlay and add text or image on top

![image](https://github.com/user-attachments/assets/71923ddf-6b5b-4b1c-af46-d12d9a525b6c)

![image](https://github.com/user-attachments/assets/e560ccf7-cc11-49ce-b6c1-8395d2e309f1)

![image](https://github.com/user-attachments/assets/815c3685-2b6d-4ef5-86c3-b2d67038736a)

![image](https://github.com/user-attachments/assets/c01d5d0e-d762-481d-ab66-7110a7101e22)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/b28900a4-4776-4aae-b631-e30334d932dd)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/e0410589-81a4-40ac-a55d-8fd6eb061721)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/2e92f213-21f9-4147-b678-0ee4dd0546ad)

![image](https://github.com/adelatour11/androidtvbackground/assets/1473994/03aecbcd-e2fd-4969-b0a2-0346d1842705)
---

## Visual styles

### Color (default) — dynamic blurred & vignette background
<img width="3840" height="2160" alt="image" src="https://github.com/user-attachments/assets/9c6fd3d9-99ec-4a7e-8845-49ce3daa7739" />

![Eddington_20250722](https://github.com/user-attachments/assets/02797fe1-5487-436b-b8c9-74c34978c3a0)

![Foundation_20250722](https://github.com/user-attachments/assets/9adcc755-879a-4b29-99dd-ce373ce141f4)

### Overlay — static canvas compositing
![image](https://github.com/user-attachments/assets/71923ddf-6b5b-4b1c-af46-d12d9a525b6c)

---

## Supported services

| Service             | What it shows                                                                           |
| ---------------------| -----------------------------------------------------------------------------------------|
| **TMDB trending**   | Weekly trending movies & TV shows from TMDB                                             |
| **Jellyfin**        | Recently added / aired media from your Jellyfin server                                  |
| **Plex**            | Recently added / aired media from your Plex server                                      |
| **Plex Friends**    | Media from Plex libraries shared with you by friends                                    |
| **Radarr / Sonarr** | Upcoming releases monitored in Radarr and Sonarr                                        |
| **Trakt**           | Movies and shows from a Trakt user list                                                 |
| **Lidarr**          | Upcoming album releases monitored in Lidarr                                             |
| **Steam**           | Recently played, unplayed (waiting to be played), and random games from a Steam library |

All generators support both visual styles (`color` and `overlay`).

---

## Quick start

### 1 — Install dependencies

```bash
pip install -r requirements.txt
```

### 2 — Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` and fill in the credentials for the service(s) you want to use.

### 3 — Run

```bash
python main.py
```

`main.py` auto-detects which services are configured and runs every enabled
generator.  Output images are written to service-specific directories
(e.g. `jellyfin_backgrounds/`, `plex_backgrounds/`).

---

## Service detection

`main.py` checks for the following environment variables and runs the
corresponding generator when all required variables are set:

| Generator | Required env vars |
|---|---|
| Jellyfin | `JELLYFIN_BASEURL` + `JELLYFIN_TOKEN` + `JELLYFIN_USER_ID` |
| Plex | `PLEX_BASEURL` + `PLEX_TOKEN` |
| Plex Friends | `PLEX_TOKEN` + `PLEX_FRIEND_ENABLED=true` |
| Radarr/Sonarr | `TMDB_BEARER_TOKEN` + `RADARR_API_KEY` and/or `SONARR_API_KEY` |
| Trakt | `TRAKT_API_KEY` + `TRAKT_USERNAME` + `TRAKT_LISTNAME` |
| Lidarr | `LIDARR_URL` + `LIDARR_API_KEY` |
| Steam | `STEAM_API_KEY` + `STEAM_USER_ID` |
| TMDB trending | `TMDB_BEARER_TOKEN` *(only when no other service ran)* |

Multiple services can run in a single execution — one generator per
configured service.

---

## Configuration reference

### Shared settings (all generators)

| Variable | Default | Description |
|---|---|---|
| `BACKGROUND_STYLE` | `color` | `color` (dynamic blurred background) or `overlay` (static canvas) |

### TMDB

| Variable | Default | Description |
|---|---|---|
| `TMDB_BEARER_TOKEN` | *(required)* | TMDB API Read Access Token (JWT). Get one at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) |
| `TMDB_BASE_URL` | `https://api.themoviedb.org/3` | TMDB REST API base URL |
| `TMDB_IMG_BASE` | `https://image.tmdb.org/t/p/original` | TMDB image CDN base URL |
| `TMDB_LANGUAGE` | `en-US` | BCP-47 language tag for titles and genres |
| `NUMBER_OF_MOVIES` | `5` | Number of movie backgrounds to generate |
| `NUMBER_OF_TV_SHOWS` | `5` | Number of TV show backgrounds to generate |
| `MAX_AGE_DAYS` | `90` | Exclude content whose release / last-air date is older than this |
| `OUTPUT_DIR` | `tmdb_backgrounds` | Output directory |
| `CUSTOM_TEXT` | `Now Trending on` | Tagline rendered on each image |

### Jellyfin

| Variable | Default | Description |
|---|---|---|
| `JELLYFIN_BASEURL` | *(required)* | Jellyfin server URL, e.g. `http://192.168.1.10:8096` |
| `JELLYFIN_TOKEN` | *(required)* | Jellyfin API key |
| `JELLYFIN_USER_ID` | *(required)* | Jellyfin user UUID |

### Plex

| Variable | Default | Description |
|---|---|---|
| `PLEX_BASEURL` | *(required)* | Plex server URL, e.g. `http://192.168.1.10:32400` |
| `PLEX_TOKEN` | *(required)* | Plex authentication token |

### Plex Friends

| Variable | Default | Description |
|---|---|---|
| `PLEX_TOKEN` | *(required)* | Same token used for Plex |
| `PLEX_FRIEND_ENABLED` | `false` | Set to `true` to enable this generator |

### Radarr / Sonarr

| Variable | Default | Description |
|---|---|---|
| `RADARR_URL` | — | Radarr base URL, e.g. `http://192.168.1.10:7878` |
| `RADARR_API_KEY` | — | Radarr API key |
| `SONARR_URL` | — | Sonarr base URL, e.g. `http://192.168.1.10:8989` |
| `SONARR_API_KEY` | — | Sonarr API key |
| `DAYS_AHEAD` | `7` | Days ahead to look for upcoming releases |
| `RADARR_SONARR_LOGO` | `jellyfinlogo.png` | Service logo file pasted on each image |

### Trakt

| Variable | Default | Description |
|---|---|---|
| `TRAKT_API_KEY` | *(required)* | Trakt API client ID |
| `TRAKT_USERNAME` | *(required)* | Trakt username |
| `TRAKT_LISTNAME` | *(required)* | Trakt list slug (URL-friendly name) |

### Lidarr

| Variable | Default | Description |
|---|---|---|
| `LIDARR_URL` | *(required)* | Lidarr base URL, e.g. `http://192.168.1.10:8686` |
| `LIDARR_API_KEY` | *(required)* | Lidarr API key (Settings → General → Security) |
| `DAYS_AHEAD` | `30` | Days ahead to look for upcoming album releases |
| `LIDARR_LOGO` | `lidarrlogo.png` | Service logo file pasted on each image |

### Steam

| Variable | Default | Description |
|---|---|---|
| `STEAM_API_KEY` | *(required)* | Steam Web API key — get one at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) |
| `STEAM_USER_ID` | *(required)* | 64-bit Steam ID (find yours at [steamid.io](https://steamid.io)) |
| `STEAM_LOGO` | `steamlogo.png` | Service logo file pasted on each image |

> **Note:** Steam's public API does not expose purchase dates. "Waiting to be played" shows
> unplayed owned games (playtime = 0) as the closest available approximation.

---

## Customising behaviour

All per-generator tuning — sort mode, item limits, labels, colours, font,
and content filters — is done in **`config.yaml`**.  No Python files need
to be edited for normal use.

```yaml
jellyfin:
  order_by: mix        # "added" | "aired" | "mix"
  limit: 10
  download_music: true
  excluded_genres: [Horror, Thriller]
  excluded_libraries: [Web Videos]
  added_label: "New or updated on"

shared:
  main_color: white
  info_color: [150, 150, 150]   # RGB list
```

### TMDB content filtering

```yaml
tmdb:
  # Exclude TV shows from these countries (ISO 3166-1 alpha-2)
  tv_excluded_countries: [jp, kr]

  # Per-country genre exclusions ('*' blocks all content from that country)
  tv_excluded_genres:
    jp: [Animation, Drama]
    kr: ["*"]

  movie_excluded_countries: [jp]
  movie_excluded_genres:
    jp: [Animation]

  # Exclude any title whose TMDB keywords include one of these strings
  excluded_keywords: [anime, adult]
```

Country codes are ISO 3166-1 alpha-2 (case-insensitive).
Genre names must match TMDB genre names exactly (e.g. `Animation`, `Drama`).

### Custom config file path

By default `config.yaml` is read from the same directory as `main.py`.
Override with the `CONFIG_FILE` env var in `.env` or the shell:

```
CONFIG_FILE=/etc/androidtvbg/config.yaml
```

---

## Project structure

```
.
├── main.py          # Entry point — run this
├── config.yaml      # All behavioural/visual settings (edit this, not main.py)
├── common.py        # All shared classes (configs, renderers, generators)
├── bckg.png         # Static canvas for 'overlay' style
├── overlay.png      # Semi-transparent overlay for 'overlay' style
├── tmdblogo.png     # TMDB watermark (used by TMDB and Trakt generators)
├── jellyfinlogo.png # Jellyfin service logo
├── plexlogo.png     # Plex service logo (white variant)
├── plexlogo_color.png  # Plex service logo (colour variant)
├── traktlogo.png    # Trakt service logo
├── lidarrlogo.png   # Lidarr service logo
├── steamlogo.png    # Steam service logo
├── .env             # Your local credentials (git-ignored)
├── .env.example     # Template — copy to .env and fill in
└── requirements.txt
```

> **Note:** `bckg.png` and `overlay.png` are only required when
> `BACKGROUND_STYLE=overlay`.  The default `color` style generates its
> canvas dynamically.

---

## Architecture

All logic lives in `common.py`.  The public classes are:

| Class | Role |
|---|---|
| `SharedConfig` | Visual/typography settings shared by all generators |
| `TMDBConfig`, `JellyfinConfig`, `PlexConfig`, `LidarrConfig`, … | Per-service credentials and options |
| `FontManager` | Downloads and caches fonts (Roboto → OpenSans → Lato → Poppins fallback chain) |
| `ImageUtils` | Static helpers: resize, clean filename, wrap text by pixel width |
| `OverlayRenderer` | Builds a canvas from `bckg.png` + `overlay.png` |
| `ColorRenderer` | Builds a canvas using blurred & vignette compositing |
| `TMDBGenerator` | TMDB trending pipeline |
| `JellyfinGenerator` | Jellyfin pipeline |
| `PlexGenerator` | Plex pipeline |
| `PlexFriendGenerator` | Plex-friend-library pipeline |
| `RadarrSonarrGenerator` | Radarr / Sonarr upcoming-releases pipeline |
| `TraktGenerator` | Trakt list pipeline |
| `LidarrGenerator` | Lidarr upcoming-albums pipeline |
| `SteamGenerator` | Steam library pipeline |

Each generator follows the same pattern:

```python
generator = SomeGenerator(service_cfg, shared_cfg, renderer, font_manager, source_dir)
generator.run()
```

`make_renderer(shared)` and `make_font_manager(shared)` are convenience
factory functions used by `main.py`.

---

## Docker

For the Docker version see the
[docker branch](https://github.com/adelatour11/androidtvbackground/tree/docker).

---

## Getting your TMDB token

1. Create a free account at [themoviedb.org](https://www.themoviedb.org)
2. Go to **Settings → API**
3. Copy the **API Read Access Token** (long JWT string) into `TMDB_BEARER_TOKEN`
