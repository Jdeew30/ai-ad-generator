from __future__ import annotations

import dataclasses
import os
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "assets"

Toggle = Union[bool, Literal["auto"]]
Layout = Literal["auto", "split", "crop", "blur"]


class ConfigError(Exception):
    pass


@dataclass
class ChannelConfig:
    name: str = "Clips FR"
    language: str = "fr"
    hashtags: list[str] = field(default_factory=lambda: ["clip", "twitchfr", "drole"])


@dataclass
class TwitchConfig:
    enabled: bool = True
    streamers: list[str] = field(default_factory=list)
    # Adds the biggest live channels of `discover_language` (needs the Twitch API keys).
    discover_live: bool = True
    discover_language: str = "fr"
    discover_limit: int = 15
    clips_per_streamer: int = 20
    window_hours: int = 168
    min_views: int = 300
    skip_title_words: list[str] = field(default_factory=list)  # titles of watch parties and ads, never downloaded


@dataclass
class YouTubeSourceConfig:
    enabled: bool = False
    channels: list[str] = field(default_factory=list)  # @handle, channel URL or channel id
    videos_per_channel: int = 3
    max_age_days: int = 7
    min_views: int = 20000
    segments_per_video: int = 2
    min_peak: float = 0.55  # "most replayed" intensity a peak needs (0..1)
    segment_min: float = 20.0
    segment_max: float = 58.0


@dataclass
class StreamerOverride:
    name: str | None = None  # display name used in titles and credits
    layout: Layout | None = None
    facecam: list[float] | None = None  # [x, y, w, h] as fractions of the frame
    skip: bool = False


@dataclass
class ShortsConfig:
    per_run: int = 1
    candidates_factor: int = 3  # how many candidates are downloaded and reviewed per short published
    min_score: float = 60.0  # Claude's mark out of 100 a clip needs to be posted
    context_before: float = 20.0  # seconds of the Twitch replay (VOD) kept before a clip, for the setup (0 = clip only)
    context_after: float = 8.0  # and after it, for the end of the reaction
    max_per_streamer_per_day: int = 2
    min_duration: float = 8.0
    max_duration: float = 90.0
    layout: Layout = "auto"
    captions: bool = True
    hook: bool = True
    hook_seconds: float = 3.0
    highlight_color: str = "#FFE600"
    credit: bool = True
    whisper_model: str = "small"
    max_fps: int = 60


@dataclass
class CompilationConfig:
    clips: int = 12
    min_clips: int = 5
    window_days: int = 7
    max_minutes: float = 15.0
    intro: bool = True


@dataclass
class AIConfig:
    enabled: Toggle = "auto"
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    review_frames: bool = True


@dataclass
class YouTubePublishConfig:
    enabled: Toggle = "auto"
    privacy: Literal["public", "unlisted", "private"] = "public"
    category_id: str = "20"  # 20 = Gaming, 24 = Entertainment, 23 = Comedy
    shorts: bool = True
    compilations: bool = True
    notify_subscribers: bool = True


@dataclass
class TikTokPublishConfig:
    enabled: Toggle = "auto"
    # inbox: the video lands in your TikTok inbox as a draft you post in one tap.
    # direct: posted directly (public only once TikTok has audited your app).
    mode: Literal["inbox", "direct"] = "inbox"
    privacy: Literal["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"] = (
        "PUBLIC_TO_EVERYONE"
    )
    allow_comments: bool = True
    allow_duet: bool = True
    allow_stitch: bool = True


@dataclass
class TelegramPublishConfig:
    enabled: Toggle = "auto"


@dataclass
class PublishConfig:
    youtube: YouTubePublishConfig = field(default_factory=YouTubePublishConfig)
    tiktok: TikTokPublishConfig = field(default_factory=TikTokPublishConfig)
    telegram: TelegramPublishConfig = field(default_factory=TelegramPublishConfig)


@dataclass
class Config:
    channel: ChannelConfig = field(default_factory=ChannelConfig)
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    youtube: YouTubeSourceConfig = field(default_factory=YouTubeSourceConfig)
    shorts: ShortsConfig = field(default_factory=ShortsConfig)
    compilation: CompilationConfig = field(default_factory=CompilationConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    # Channels that asked not to be clipped: never downloaded, whatever the source.
    blocklist: list[str] = field(default_factory=list)
    streamers: dict[str, StreamerOverride] = field(default_factory=dict)

    def override_for(self, login: str) -> StreamerOverride:
        return self.streamers.get(login.lower()) or StreamerOverride()

    def is_blocked(self, login: str) -> bool:
        login = login.lower()
        return login in {b.lower().lstrip("@") for b in self.blocklist} or self.override_for(login).skip


def _coerce(tp: typing.Any, value: typing.Any, where: str) -> typing.Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if dataclasses.is_dataclass(tp):
        return build(tp, value, where)
    if origin in (Union, types.UnionType):
        if value is None and type(None) in args:
            return None
        errors: list[str] = []
        for option in args:
            if option is type(None):
                continue
            try:
                return _coerce(option, value, where)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError(errors[-1] if errors else f"{where} : valeur invalide")
    if origin is Literal:
        if value in args:
            return value
        raise ConfigError(f"{where} : {value!r} n'est pas une valeur autorisée ({', '.join(map(str, args))})")
    if origin is list:
        if not isinstance(value, list):
            raise ConfigError(f"{where} : une liste est attendue")
        return [_coerce(args[0], item, f"{where}[{i}]") for i, item in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{where} : un objet est attendu")
        return {str(k).lower(): _coerce(args[1], v, f"{where}.{k}") for k, v in value.items()}
    if tp is bool:
        if isinstance(value, bool):
            return value
        raise ConfigError(f"{where} : true ou false attendu")
    if tp is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ConfigError(f"{where} : un entier est attendu")
    if tp is float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ConfigError(f"{where} : un nombre est attendu")
    if tp is str:
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        raise ConfigError(f"{where} : un texte est attendu")
    raise ConfigError(f"{where} : type non géré ({tp})")


def build(cls: type, data: typing.Any, where: str = "config") -> typing.Any:
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ConfigError(f"{where} : un objet est attendu")
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - names)
    if unknown:
        raise ConfigError(f"{where} : clé(s) inconnue(s) : {', '.join(map(str, unknown))}")
    values = {name: _coerce(hints[name], data[name], f"{where}.{name}") for name in names if name in data}
    return cls(**values)


def load_config(path: Path | None = None) -> Config:
    path = path or PROJECT_DIR / "config.yaml"
    if not path.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} n'est pas un YAML valide : {exc}") from exc
    config = build(Config, data or {}, "config")
    config.twitch.streamers = [s.lower().lstrip("@") for s in config.twitch.streamers]
    if config.shorts.per_run < 0 or config.shorts.candidates_factor < 1:
        raise ConfigError("config.shorts : per_run >= 0 et candidates_factor >= 1 attendus")
    if not 0 <= config.shorts.min_score <= 100:
        raise ConfigError("config.shorts.min_score : une note entre 0 et 100 attendue")
    if config.shorts.context_before < 0 or config.shorts.context_after < 0:
        raise ConfigError("config.shorts : context_before et context_after >= 0 attendus (en secondes)")
    for login, override in config.streamers.items():
        if override.facecam is not None and (
            len(override.facecam) != 4 or not all(0 <= v <= 1 for v in override.facecam)
        ):
            raise ConfigError(f"config.streamers.{login}.facecam : [x, y, largeur, hauteur] entre 0 et 1 attendu")
    return config


@dataclass(frozen=True)
class Secrets:
    """Credentials, read from the environment (GitHub secrets in CI, a .env file locally)."""

    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    anthropic_api_key: str | None = None
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_refresh_token: str | None = None
    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None
    tiktok_refresh_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    gh_secrets_token: str | None = None  # lets the bot store a rotated TikTok refresh token
    github_repository: str | None = None
    ytdlp_cookies: str | None = None  # Netscape cookies file content or path, for YouTube

    @classmethod
    def from_env(cls, env: typing.Mapping[str, str] | None = None) -> Secrets:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = (env.get(name) or "").strip()
            return value or None

        return cls(**{f.name: get(f.name.upper()) for f in dataclasses.fields(cls)})

    @property
    def twitch_api(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    @property
    def youtube(self) -> bool:
        return bool(self.youtube_client_id and self.youtube_client_secret and self.youtube_refresh_token)

    @property
    def tiktok(self) -> bool:
        return bool(self.tiktok_client_key and self.tiktok_client_secret and self.tiktok_refresh_token)

    @property
    def telegram(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE loader for local runs; never overrides real environment variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Paths:
    data: Path
    state: Path
    work: Path
    output: Path

    @classmethod
    def resolve(cls, env: typing.Mapping[str, str] | None = None) -> Paths:
        env = os.environ if env is None else env
        data = Path(env.get("CLIPPER_DATA_DIR") or PROJECT_DIR / "data").resolve()
        state = Path(env.get("CLIPPER_STATE_PATH") or data / "state.json").resolve()
        output = Path(env.get("CLIPPER_OUTPUT_DIR") or PROJECT_DIR / "output").resolve()
        return cls(data=data, state=state, work=data / "work", output=output)
