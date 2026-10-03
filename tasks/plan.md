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

# Plan: quant

Spec: `SPEC-quant.md`. Un archivo (`quant.py`) + un test (`tests/test_quant.py`). Sin dependencias nuevas.

Orden (cada paso: test rojo → código → verde):
1. Funciones puras: `num`, `positive`, `median`, `relative`, `yoy`, `avg_volume`.
2. Capa HTTP `fetch` memoizada: apikey fuera de los errores, `FMPError` con status HTTP, contador `calls_made`.
3. `analyze` sin pares: TTM, histórico, último periodo (con fallback a FY) y volumen.
4. Pares: lista de FMP o de config, descarte de peers fallidos, `MIN_PEERS`, caché compartida.
5. CLI `python -m quant AAPL` contra FMP real → commit.

Riesgos:
- Fuga de la apikey (va en la query string) → nunca formatear URL ni encadenar la excepción; test que lo comprueba.
- Orden de las series de FMP (más reciente primero) → el volumen se ordena por `date`; el trimestral se indexa 0 vs 4 (verificado).
- Cuota diaria → caché por ejecución y `calls_made` visible en el CLI.
- `functools.cache` no cachea excepciones: un peer fallido se reintentaría en otro `analyze` (aceptable).
