# Spec: notify

Estado: **APROBADO** (2026-10-03)
Módulo hoja, sin dependencias. Convenciones comunes en `CAPABILITY_MAP.md`.

## Objetivo

Enviar texto al móvil del usuario mediante un bot de Telegram. Es el único canal de salida del sistema: `radar` lo usa tanto para el resumen periódico como para la alerta prioritaria de nuevo filing. Construido primero para poder ver resultados reales en el móvil desde el inicio.

## Interfaz pública

```python
def send(text: str, *, silent: bool = False) -> None:
    """Envía text al chat configurado; trocea si supera el límite de Telegram."""

def split_message(text: str, limit: int = 4096) -> list[str]:
    """Trocea text en partes <= limit, cortando por saltos de línea si es posible."""
```

- Configuración por entorno: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- `silent=True` → `disable_notification` (pensado para el resumen periódico; la alerta prioritaria suena).
- Texto plano (sin `parse_mode`): el texto del LLM contiene `* _ [ ] ( ) .` que romperían MarkdownV2.
- CLI de prueba: `python -m notify "texto"`. Carga antes `.env` de la carpeta actual.

```python
def load_env(path: str = ".env") -> None:
    """Carga KEY=VALUE de path en os.environ sin pisar variables ya definidas."""
```
- Ignora líneas vacías y `#`, quita comillas y BOM; si el archivo no existe, no hace nada. `radar` lo reutilizará.

## Comportamiento

1. `split_message`:
   - Texto ≤ `limit` → una sola parte, sin modificar.
   - Texto mayor → se acumulan líneas completas hasta `limit`; una línea más larga que `limit` se corta en trozos de `limit`.
   - Nunca devuelve partes vacías ni pierde caracteres (salvo el `\n` usado como punto de corte).
2. `send`: un `POST https://api.telegram.org/bot<TOKEN>/sendMessage` por parte, en orden, JSON `{chat_id, text, disable_notification}`, timeout 10 s.
3. Errores:
   - Falta una variable de entorno → `RuntimeError` que nombra la variable (nunca su valor).
   - HTTP ≠ 2xx o respuesta `"ok": false` → `RuntimeError` con el `description` de Telegram. El mensaje no contiene el token.
   - Sin reintentos: `radar` decide qué hacer ante el fallo.

## Tests (`tests/test_notify.py`)

- `split_message`: texto corto; texto justo en el límite; varias líneas que exigen corte; línea única más larga que el límite; la concatenación de partes conserva el contenido.
- `send` con `urlopen` parcheado: payload correcto (`chat_id`, `text`, `disable_notification`); N partes → N peticiones en orden; `ok:false` → `RuntimeError` sin el token en el mensaje; variable de entorno ausente → `RuntimeError` con su nombre.

## Criterios de éxito

- [x] `.venv\Scripts\python -m unittest discover -s tests -v` en verde.
- [x] `.venv\Scripts\python -m notify "hola radar"` hace llegar el mensaje al móvil (verificación manual con un bot real).
- [x] Un texto de 10 000 caracteres llega como 3 mensajes ordenados, ninguno rechazado por Telegram.
- [x] Ningún error ni log contiene el token.

## Fuera de alcance

Formato enriquecido (Markdown/HTML), botones, recibir mensajes o comandos del bot, reintentos/backoff ante 429, otros canales push. Se añadirán si `radar` los necesita.

## Decisiones

1. Resuelto: bot y `chat_id` configurados en `.env`.
2. Resuelto: resumen periódico `silent=True`; alerta de nuevo 10-K/10-Q `silent=False`.
