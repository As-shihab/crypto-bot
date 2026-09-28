"""
API keys, secrets and service URLs, stored in the MySQL `credentials` table
and edited on the dashboard's Settings page — .env only holds the MySQL login.

Same pattern as settings.py: config.py holds the defaults, `apply_saved()`
loads the table onto the config module at startup and `update()` saves +
applies a change immediately (every caller reads `config.X` at call time).

Secrets are never sent back to the browser: `public_view()` returns them
masked, and a blank secret in `update()` means "keep the current value".
"""

from __future__ import annotations

import config
import db
import notifier

# name -> (provider, config attribute, type, is_secret, label, help)
FIELDS = {
    "BINANCE_API_KEY":    ("binance", "BINANCE_API_KEY", str, True, "API key", "Spot trading permission only — never withdrawals. IP-restrict it."),
    "BINANCE_API_SECRET": ("binance", "BINANCE_API_SECRET", str, True, "API secret", "Shown once by Binance when the key is created."),
    "BINANCE_API_URL":    ("binance", "BINANCE_API_URL", "url", False, "API URL", "Spot REST base URL for orders and prices, e.g. https://api.binance.com. Charts pick up a change after a restart."),
    "TELEGRAM_API_URL":   ("telegram", "TELEGRAM_API_URL", "url", False, "API URL", "Telegram Bot API base URL."),
    "TELEGRAM_BOT_TOKEN": ("telegram", "TELEGRAM_BOT_TOKEN", str, True, "Bot token", "From @BotFather. Blank = console-only signals."),
    "TELEGRAM_CHAT_ID":   ("telegram", "TELEGRAM_CHAT_ID", str, False, "Chat ID", "Where signal alerts are sent."),
    "SMTP_HOST":          ("smtp", "SMTP_HOST", str, False, "SMTP host", "e.g. smtp.gmail.com. Blank = no emails."),
    "SMTP_PORT":          ("smtp", "SMTP_PORT", int, False, "SMTP port", "587 (STARTTLS) or 465 (SSL)."),
    "SMTP_USE_SSL":       ("smtp", "SMTP_USE_SSL", bool, False, "Use SSL", "On for port 465, off for STARTTLS on 587."),
    "SMTP_USER":          ("smtp", "SMTP_USER", str, False, "SMTP user", "Login, usually your email address."),
    "SMTP_PASSWORD":      ("smtp", "SMTP_PASSWORD", str, True, "SMTP password", "Gmail: an App Password, not your normal password."),
    "EMAIL_FROM":         ("smtp", "EMAIL_FROM", str, False, "From", "Sender address (defaults to the SMTP user)."),
    "EMAIL_TO":           ("smtp", "EMAIL_TO", str, False, "To", "Where trade open/close emails go."),
}
PROVIDERS = {"binance": "Binance", "telegram": "Telegram", "smtp": "Email (SMTP)"}
BINANCE_FIELDS = ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_API_URL")
_DEFAULTS = {name: getattr(config, attr) for name, (_p, attr, *_rest) in FIELDS.items()}


def _to_text(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return "" if value is None else str(value).strip()


def _coerce(name: str, text: str):
    _p, _attr, typ, _secret, label, _help = FIELDS[name]
    if typ is bool:
        return text.lower() in ("1", "true", "yes", "on")
    if typ is int:
        try:
            return int(text)
        except ValueError:
            raise ValueError(f"{label}: not a whole number")
    if typ == "url":
        if not text.startswith(("https://", "http://")):
            raise ValueError(f"{label}: must start with https://")
        return text.rstrip("/")
    return text


def _apply(name: str, text: str):
    value = _coerce(name, text)
    if name == "EMAIL_FROM" and not value:
        value = config.SMTP_USER
    setattr(config, FIELDS[name][1], value)


def apply_saved():
    """Create missing rows with their defaults, then load the table onto config (call once at startup)."""
    db.init_db()
    for name, (provider, _attr, _typ, secret, *_rest) in FIELDS.items():
        db.seed_credential(name, provider, _to_text(_DEFAULTS[name]), secret)
    saved = db.get_credentials()
    for name in FIELDS:     # SMTP_USER before EMAIL_FROM so its fallback sees the saved user
        if name in saved:
            try:
                _apply(name, saved[name])
            except ValueError:
                pass    # a value that no longer validates keeps the default


def _mask(text: str) -> str:
    if not text:
        return ""
    return "••••" + text[-4:] if len(text) > 8 else "••••"


def binance_ready() -> bool:
    return bool(config.BINANCE_API_KEY and config.BINANCE_API_SECRET)


def public_view() -> dict:
    """What the Settings page shows: plain values, secrets masked (never the secret itself)."""
    saved = db.get_credentials()
    fields = []
    for name, (provider, _attr, typ, secret, label, help_) in FIELDS.items():
        text = saved.get(name, _to_text(_DEFAULTS[name]))
        fields.append({
            "name": name, "provider": provider, "label": label, "help": help_, "secret": secret,
            "type": typ if isinstance(typ, str) else typ.__name__,
            "value": "" if secret else text,
            "masked": _mask(text) if secret else None,
            "is_set": bool(text),
        })
    return {"fields": fields, "providers": PROVIDERS,
            "binance_ready": binance_ready(), "email_configured": notifier.email_configured()}


def update(changes: dict) -> tuple[list[str], list[str]]:
    """Validate + save + apply. Returns (changed names, errors). Nothing is saved if any value is invalid.

    A blank secret keeps the stored one; send {"clear": ["NAME"]} to erase a secret."""
    changes = dict(changes or {})
    clear = set(changes.pop("clear", None) or [])
    staged, errors = {}, []
    for name, value in changes.items():
        if name not in FIELDS:
            errors.append(f"Unknown credential {name}")
            continue
        text = _to_text(value)
        if FIELDS[name][3] and not text:
            continue    # blank secret field = unchanged
        try:
            staged[name] = _to_text(_coerce(name, text))   # store the normalized form
        except ValueError as exc:
            errors.append(str(exc))
    for name in clear:
        if name in FIELDS and FIELDS[name][3]:
            staged[name] = ""
    if errors:
        return [], errors
    saved = db.get_credentials()
    changed = [n for n, t in staged.items() if saved.get(n) != t]
    for name in changed:
        provider, _attr, _typ, secret, *_rest = FIELDS[name]
        db.set_credential(name, provider, staged[name], secret)
        saved[name] = staged[name]
    for name in FIELDS:     # re-apply in order so EMAIL_FROM's fallback follows SMTP_USER
        if name in changed or (name == "EMAIL_FROM" and "SMTP_USER" in changed):
            _apply(name, saved.get(name, ""))
    return changed, []
