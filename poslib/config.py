"""
config.py - loads config.yaml and the secrets in .env.

Two separate places on purpose:

  config.yaml  settings you are meant to read and change. Safe to share,
               safe to copy, safe to put in version control.
  .env         passwords and API tokens. Never shared, never committed.

Everything in the tool reads its settings through here, so there is exactly
one place where a threshold or a path is defined.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

# The folder this project lives in (the parent of poslib/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Something in config.yaml is missing or does not make sense."""


def _load_env(path: Path) -> dict[str, str]:
    """
    Read a .env file into a dictionary.

    Deliberately simple: KEY=value, one per line, # starts a comment.
    Values may be wrapped in quotes. We do not need anything cleverer, and
    not depending on an extra library is one less thing to install.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key] = val

    return values


class Config:
    """
    The tool's settings.

    Read values with dotted paths:

        cfg.get("thresholds.customers.lapsed_days")     -> 90
        cfg.secret("SMTP_PASSWORD")                     -> from .env only
    """

    def __init__(self, config_path: str | Path | None = None,
                 env_path: str | Path | None = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.env_path = Path(env_path) if env_path else DEFAULT_ENV_PATH

        if not self.config_path.is_file():
            raise ConfigError(
                f"Cannot find the settings file: {self.config_path}\n"
                "It should sit next to start.bat. If it is missing, copy it "
                "back from the original download."
            )

        try:
            raw = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"config.yaml could not be read - there is a typo in it.\n{exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise ConfigError("config.yaml is empty or malformed.")

        self._data: dict[str, Any] = raw
        self._env: dict[str, str] = _load_env(self.env_path)

        self._validate()

    # -- reading values ----------------------------------------------------

    def get(self, dotted: str, default: Any = None) -> Any:
        """Read a setting by dotted path, e.g. "digest.channels.email.enabled"."""
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, None)
        if value is None:
            raise ConfigError(f"config.yaml is missing the setting: {dotted}")
        return value

    def secret(self, name: str, default: str = "") -> str:
        """
        Read a password or token.

        Looked for in .env first, then in the machine's environment variables.
        Never read from config.yaml - secrets do not belong there.
        """
        if name in self._env:
            return self._env[name]
        return os.environ.get(name, default)

    def has_secret(self, name: str) -> bool:
        return bool(self.secret(name))

    # -- paths -------------------------------------------------------------

    def path(self, dotted: str, default: str = "") -> Path:
        """
        Read a setting that is a file or folder path and turn it into a full
        path. Relative paths are taken as relative to the project folder, so
        the tool works the same however it is started.
        """
        raw = str(self.get(dotted, default) or default)
        p = Path(raw)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        return p

    @property
    def source_db(self) -> Path:
        return self.path("database.path")

    @property
    def cache_db(self) -> Path:
        return self.path("database.cache", "cache.db")

    @property
    def digest_dir(self) -> Path:
        return self.path("digest.output_dir", "digests")

    @property
    def log_file(self) -> Path:
        return self.path("logging.file", "logs/pos-tool.log")

    # -- convenience -------------------------------------------------------

    @property
    def languages(self) -> list[str]:
        return ["en", "fr", "ar"]

    @property
    def default_language(self) -> str:
        lang = str(self.get("interface.default_language", "fr")).lower()
        return lang if lang in self.languages else "fr"

    @property
    def currency(self) -> str:
        return str(self.get("business.currency", "DZD"))

    @property
    def currency_symbol(self) -> str:
        return str(self.get("business.currency_symbol", "DA"))

    def digest_language(self) -> str:
        lang = str(self.get("digest.language", "follow_interface")).lower()
        if lang in self.languages:
            return lang
        return self.default_language

    def channel_enabled(self, name: str) -> bool:
        # The file channel is the fallback that must always work. It cannot
        # be switched off, whatever the settings file says.
        if name == "file":
            return True
        return bool(self.get(f"digest.channels.{name}.enabled", False))

    # -- checks ------------------------------------------------------------

    def _validate(self) -> None:
        """
        Catch the mistakes that would otherwise show up much later as a
        confusing error, and say something useful about them.
        """
        problems: list[str] = []

        if not self.get("database.path"):
            problems.append("database.path is not set - the tool does not know "
                            "where your POS database is.")

        lang = str(self.get("interface.default_language", "fr")).lower()
        if lang not in self.languages:
            problems.append(f"interface.default_language is '{lang}'. "
                            f"It must be one of: en, fr, ar.")

        port = self.get("interface.port", 8777)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            problems.append(f"interface.port is '{port}'. It must be a number "
                            "between 1 and 65535.")

        hour = self.get("digest.hour", 20)
        minute = self.get("digest.minute", 0)
        if not isinstance(hour, int) or not (0 <= hour <= 23):
            problems.append(f"digest.hour is '{hour}'. It must be 0 to 23.")
        if not isinstance(minute, int) or not (0 <= minute <= 59):
            problems.append(f"digest.minute is '{minute}'. It must be 0 to 59.")

        backup_hour = self.get("backup.hour", 3)
        backup_minute = self.get("backup.minute", 0)
        if not isinstance(backup_hour, int) or not (0 <= backup_hour <= 23):
            problems.append(f"backup.hour is '{backup_hour}'. It must be 0 to 23.")
        if not isinstance(backup_minute, int) or not (0 <= backup_minute <= 59):
            problems.append(f"backup.minute is '{backup_minute}'. It must be 0 to 59.")

        for key in ("thresholds.inventory.dead_stock_days",
                    "thresholds.customers.lapsed_days",
                    "thresholds.customers.collection_risk_days",
                    "thresholds.customers.credit_risk_medium_days",
                    "thresholds.customers.credit_risk_high_days",
                    "catalog.new_arrivals_days",
                    "backup.keep_days"):
            val = self.get(key)
            if val is not None and (not isinstance(val, (int, float)) or val <= 0):
                problems.append(f"{key} is '{val}'. It must be a positive number of days.")

        pp = self.get("thresholds.margin.family_benchmark_pp")
        if pp is not None and (not isinstance(pp, (int, float)) or pp <= 0):
            problems.append(f"thresholds.margin.family_benchmark_pp is '{pp}'. "
                            "It must be a positive number of percentage points.")

        push_interval = self.get("remote.push_interval_seconds")
        if push_interval is not None and (not isinstance(push_interval, (int, float))
                                          or push_interval <= 0):
            problems.append(f"remote.push_interval_seconds is '{push_interval}'. "
                            "It must be a positive number of seconds.")

        if problems:
            raise ConfigError(
                "There are problems in config.yaml:\n\n  - "
                + "\n  - ".join(problems)
            )

    def describe_channels(self) -> list[dict[str, Any]]:
        """
        A plain-language summary of which digest channels will actually run,
        and why one might not. Shown on the dashboard so it is never a
        mystery why no email arrived.
        """
        out: list[dict[str, Any]] = []

        out.append({
            "name": "file",
            "enabled": True,
            "ready": True,
            "note": "Always on. Saves the digest to the digests folder.",
        })

        email_on = self.channel_enabled("email")
        has_pwd = self.has_secret("SMTP_PASSWORD")
        has_from = bool(self.get("digest.channels.email.from_address"))
        has_to = bool(self.get("digest.channels.email.to_addresses"))
        out.append({
            "name": "email",
            "enabled": email_on,
            "ready": email_on and has_pwd and has_from and has_to,
            "note": (
                "Switched off in config.yaml." if not email_on else
                "No SMTP_PASSWORD in the .env file." if not has_pwd else
                "No from_address set in config.yaml." if not has_from else
                "No to_addresses set in config.yaml." if not has_to else
                "Ready."
            ),
        })

        tg_on = self.channel_enabled("telegram")
        tg_tok = self.has_secret("TELEGRAM_BOT_TOKEN")
        tg_chat = self.has_secret("TELEGRAM_CHAT_ID")
        out.append({
            "name": "telegram",
            "enabled": tg_on,
            "ready": tg_on and tg_tok and tg_chat,
            "note": (
                "Switched off in config.yaml." if not tg_on else
                "No TELEGRAM_BOT_TOKEN in the .env file." if not tg_tok else
                "No TELEGRAM_CHAT_ID in the .env file." if not tg_chat else
                "Ready."
            ),
        })

        wa_on = self.channel_enabled("whatsapp")
        wa_tok = self.has_secret("WHATSAPP_ACCESS_TOKEN")
        wa_pid = bool(self.get("digest.channels.whatsapp.phone_number_id"))
        wa_to = bool(self.get("digest.channels.whatsapp.to_numbers"))
        out.append({
            "name": "whatsapp",
            "enabled": wa_on,
            "ready": wa_on and wa_tok and wa_pid and wa_to,
            "note": (
                "Switched off in config.yaml." if not wa_on else
                "No WHATSAPP_ACCESS_TOKEN in the .env file." if not wa_tok else
                "No phone_number_id set in config.yaml." if not wa_pid else
                "No to_numbers set in config.yaml." if not wa_to else
                "Ready."
            ),
        })

        return out


# A single shared instance, created on first use.
_INSTANCE: Config | None = None


def get_config(reload: bool = False) -> Config:
    global _INSTANCE
    if _INSTANCE is None or reload:
        _INSTANCE = Config()
    return _INSTANCE


def setup_logging(cfg: Config | None = None) -> None:
    """Send log messages both to the screen and to a file we keep."""
    cfg = cfg or get_config()
    level_name = str(cfg.get("logging.level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = cfg.log_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:      # already set up
        return
    root.setLevel(level)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        from logging.handlers import TimedRotatingFileHandler
        fileh = TimedRotatingFileHandler(
            log_path, when="midnight",
            backupCount=int(cfg.get("logging.keep_days", 30)),
            encoding="utf-8")
        fileh.setFormatter(fmt)
        root.addHandler(fileh)
    except OSError:
        # If the log file cannot be opened we still want the tool to run.
        root.warning("Could not open the log file at %s - continuing without it.", log_path)
