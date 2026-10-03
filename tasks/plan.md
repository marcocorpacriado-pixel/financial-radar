# Plan: notify

Spec: `SPEC-notify.md`. Un archivo (`notify.py`) + un test (`tests/test_notify.py`). Sin dependencias.

Orden (cada paso: test rojo → código → verde):
1. `split_message` — función pura, base de `send`.
2. `send` — usa `split_message`; red simulada con `urlopen` parcheado.
3. `load_env` + CLI `__main__` — necesario para la verificación manual.
4. Verificación manual contra Telegram real → commit.

Riesgos:
- Fuga del token: va dentro de la URL. Mitigación: nunca formatear la URL ni la excepción `HTTPError` en mensajes; test que lo comprueba.
- `.env` guardado con BOM por editores de Windows → leer con `utf-8-sig`.
- Errores HTTP de Telegram traen JSON con `description`; un 5xx de proxy puede traer HTML → fallback a `HTTP <código>`.
