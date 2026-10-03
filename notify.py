"""Envío de mensajes a Telegram (Bot API sendMessage). Spec: SPEC-notify.md."""
import json
import os
import sys
import urllib.error
import urllib.request

LIMIT = 4096


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Trocea text en partes <= limit, cortando por saltos de línea si es posible."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut > 0:
            parts.append(text[:cut])
            text = text[cut + 1:]
        else:
            parts.append(text[:limit])
            text = text[limit:]
    if text:
        parts.append(text)
    return parts


def send(text: str, *, silent: bool = False) -> None:
    """Envía text al chat configurado; trocea si supera el límite de Telegram."""
    token, chat_id = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in split_message(text):
        body = json.dumps({"chat_id": chat_id, "text": part, "disable_notification": silent}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        # Nunca se encadena ni se formatea la excepción original: su URL lleva el token.
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                data = json.load(e)
            except ValueError:
                data = {"description": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            data = {"description": f"sin conexión ({e.reason})"}
        if not data.get("ok"):
            raise RuntimeError(f"Telegram: {data.get('description', 'error desconocido')}")


def load_env(path: str = ".env") -> None:
    """Carga KEY=VALUE de path en os.environ sin pisar variables ya definidas."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}")
    return value


if __name__ == "__main__":
    load_env()
    send(" ".join(sys.argv[1:]) or "radar: prueba de notify")
