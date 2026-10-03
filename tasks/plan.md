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

# Plan: sec-mdna

Spec: `SPEC-sec-mdna.md`. Un archivo (`sec_mdna.py`) + un test (`tests/test_sec_mdna.py`). Sin dependencias nuevas.

Orden (cada paso: test rojo → código → verde):
1. `html_to_text`: HTMLParser con descarte de ocultos, líneas de bloque, filas de tabla y fusión de `$ ) %`.
2. `extract_section`: candidatos de encabezado + regla de la sección más larga + marcadores de fin.
3. HTTP SEC (`_get`: User-Agent, gzip, pausa), CIK con caché y `filings`.
4. `fetch_mdna` con caché en disco y anterior opcional.
5. CLI + verificación real (AAPL 10-Q/10-K, MSFT, NVDA) → commit.

Riesgos:
- Maquetaciones distintas entre emisores → la regla de la sección más larga no depende del formato del índice; se verifica con 3 emisores reales.
- Elemento vacío (`<meta>`, `<br>`) con `display:none` que nunca cierra y se tragaría el resto → los elementos vacíos no abren zona oculta.
- Encabezado partido en dos líneas ("Item 7." / "Management's…") → el patrón se evalúa sobre la línea y la siguiente.

# Plan: analyst

Spec: `SPEC-analyst.md`. Un archivo (`analyst.py`) + un test (`tests/test_analyst.py`). Sin dependencias nuevas.

Orden (cada paso: test rojo → código → verde):
1. `prune_mdna` (pura) y medición sobre los MD&A reales en caché (AAPL, MSFT).
2. `quote_found` (pura): normalización y reglas de rechazo.
3. `build_request` + `_post` (cabeceras, reintentos 429/529/5xx, 401 sin reintento).
4. `parse_response` + `summarize_mdna` (stop reasons, estructura, verificación, límite por categoría).
5. `render` (≤ 10 líneas, alternancia de categorías).
6. CLI + verificación real con un filing (AAPL 10-Q actual vs anterior) → commit.

Riesgos:
- La regla de repetición puede borrar subtítulos legítimos repetidos (p. ej. nombres de segmento) → se mide sobre los textos reales antes de fijar el umbral.
- Coste de la verificación real (~$0,10 por análisis) → una sola llamada real.
- Respuesta con bloques `thinking`/`fallback` antes del texto → se toma el primer bloque `text`.
