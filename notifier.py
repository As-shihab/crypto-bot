"""
Email notifications (SMTP) for the auto trader: one email per trade open,
per close, and for halts/errors. Sends on a background thread so a slow
mail server never delays an order. Silently console-only if SMTP isn't
configured in .env.
"""

from __future__ import annotations
import smtplib
import ssl
import threading
from email.message import EmailMessage

import config


def email_configured() -> bool:
    return bool(config.SMTP_HOST and config.EMAIL_TO and config.EMAIL_FROM)


def _send(subject: str, body: str) -> str | None:
    """Send one email; returns None on success or the error text."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        if config.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=20)
        else:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
            server.starttls(context=ctx)
        with server:
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return None
    except Exception as exc:
        print(f"[notifier] email failed: {exc}")
        return str(exc)


def send_email(subject: str, body: str, wait: bool = False) -> str | None:
    """Queue an email (background thread). With wait=True, block and return None or the error."""
    print(f"[notifier] {subject}")
    if not email_configured():
        return "Email not configured (SMTP_HOST / EMAIL_FROM / EMAIL_TO in .env)"
    if wait:
        return _send(subject, body)
    threading.Thread(target=_send, args=(subject, body), daemon=True).start()
    return None


def _fmt(v, nd=4):
    return "—" if v is None else f"{v:,.{nd}f}"


def trade_opened(t: dict):
    subject = f"[{t['mode'].upper()}] OPEN LONG {t['symbol']} {t['timeframe']} @ {_fmt(t['entry_price'])}"
    body = "\n".join([
        f"Trade #{t['id']} opened ({t['mode']} mode)",
        "",
        f"Symbol:      {t['symbol']}  ({t['timeframe']})",
        f"Side:        {t['side']}",
        f"Strategy:    {t.get('strategy') or 'rules'}",
        f"Quantity:    {t['qty']:.8f}",
        f"Entry:       {_fmt(t['entry_price'])}",
        f"Cost:        {_fmt(t['cost_usd'], 2)} {config.QUOTE_ASSET} (incl. fee)",
        f"Stop:        {_fmt(t['stop_price'])}",
        f"TP1:         {_fmt(t['tp1_price'])}  (sell {config.TP1_CLOSE_FRACTION:.0%}, stop -> breakeven)",
        f"TP2:         {_fmt(t['target_price'])}",
        f"Quality:     {t['quality']:.0f}/100" if t.get("quality") is not None else "Quality:     — (your own order)",
        "",
        "Why:",
        *[f"  - {r}" for r in (t.get("reasons_list") or [])],
    ])
    send_email(subject, body)


def trade_closed(t: dict):
    pnl = t.get("pnl_usd") or 0.0
    sign = "+" if pnl >= 0 else ""
    subject = (f"[{t['mode'].upper()}] CLOSE {t['symbol']} {t['timeframe']} "
               f"{sign}{pnl:.2f} {config.QUOTE_ASSET} ({sign}{(t.get('pnl_pct') or 0):.2f}%) — {t['exit_reason']}")
    held_min = ((t.get("exit_time") or 0) - t["entry_time"]) / 60
    body = "\n".join([
        f"Trade #{t['id']} closed ({t['mode']} mode)",
        "",
        f"Symbol:      {t['symbol']}  ({t['timeframe']})",
        f"Strategy:    {t.get('strategy') or 'rules'}",
        f"Reason:      {t['exit_reason']}",
        f"Entry:       {_fmt(t['entry_price'])}",
        f"Exit (avg):  {_fmt(t.get('exit_price'))}",
        f"Quantity:    {t['qty']:.8f}",
        f"P&L:         {sign}{pnl:.2f} {config.QUOTE_ASSET} ({sign}{(t.get('pnl_pct') or 0):.2f}%)",
        f"Fees:        {t['fees_usd']:.4f} {config.QUOTE_ASSET}",
        f"Held:        {held_min:.0f} min",
    ])
    send_email(subject, body)


def alert(subject: str, body: str = "", mode: str | None = None):
    send_email(f"[{(mode or config.TRADING_MODE).upper()}] {subject}", body or subject)
