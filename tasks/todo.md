# Tareas

## notify

- [x] Task 1: `split_message`
  - Acceptance: partes ≤ límite, cortes por `\n` cuando es posible, líneas largas cortadas en duro, sin partes vacías, sin pérdida de contenido.
  - Verify: `.venv\Scripts\python -m unittest tests.test_notify -v`
  - Files: `notify.py`, `tests/test_notify.py`
- [x] Task 2: `send(text, *, silent=False)`
  - Acceptance: un POST por parte en orden con `chat_id`, `text`, `disable_notification`; timeout 10 s; `RuntimeError` ante variable ausente, HTTP ≠ 2xx u `ok:false`, nunca con el token.
  - Verify: igual que Task 1
  - Files: `notify.py`, `tests/test_notify.py`
- [x] Task 3: `load_env` + `python -m notify "texto"`
  - Acceptance: carga `.env` (comentarios, comillas, BOM) sin pisar variables existentes; archivo ausente no falla.
  - Verify: igual que Task 1
  - Files: `notify.py`, `tests/test_notify.py`
- [x] Task 4: verificación manual + commit
  - Acceptance: llega "hola radar" al móvil; un texto de 10 000 caracteres llega en 3 mensajes ordenados.
  - Verify: `.venv\Scripts\python -m notify "hola radar"`
  - Files: —

## quant

- [x] Task 0: smoke test de endpoints FMP con la key real
  - Acceptance: los 7 endpoints responden y los campos de la spec existen.
  - Verify: script exploratorio (sin commitear).
- [x] Task 1: funciones puras de cálculo
  - Acceptance: reglas de la tabla de `SPEC-quant.md` (None, ≤ 0, NaN, < 31 sesiones…).
  - Verify: `.venv\Scripts\python -m unittest tests.test_quant -v`
  - Files: `quant.py`, `tests/test_quant.py`
- [x] Task 2: `fetch` memoizada + errores
  - Acceptance: una llamada por (path, params); `RuntimeError`/`FMPError` sin apikey; falta de key nombrada.
  - Verify: igual que Task 1
  - Files: `quant.py`, `tests/test_quant.py`
- [x] Task 3: `analyze` del ticker (TTM, histórico, último periodo con fallback FY, volumen)
  - Acceptance: Report con valores calculados a mano en el test; fallback ante 403 y lista vacía.
  - Verify: igual que Task 1
  - Files: `quant.py`, `tests/test_quant.py`
- [x] Task 4: pares
  - Acceptance: config sin `stock-peers`, se excluye el propio ticker, peer fallido descartado, `MIN_PEERS`, caché compartida.
  - Verify: igual que Task 1
  - Files: `quant.py`, `tests/test_quant.py`
- [x] Task 5: CLI + verificación real + commit
  - Acceptance: `python -m quant AAPL` sin nulos, ≤ 12 llamadas, valores coherentes con FMP.
  - Verify: `.venv\Scripts\python -m quant AAPL`

## sec-mdna

- [x] Task 1: `html_to_text`
  - Acceptance: ocultos fuera, bloques como líneas, `<br>` en celda como espacio, filas `a | b`, `$78,678`, `(1,234)`, `12%`.
  - Verify: `.venv\Scripts\python -m unittest tests.test_sec_mdna -v`
  - Files: `sec_mdna.py`, `tests/test_sec_mdna.py`
- [x] Task 2: `extract_section`
  - Acceptance: índice frente a cuerpo, 7 frente a 7A, Item 2 de la Parte II, marcadores de fin, `MIN_CHARS`.
  - Verify: igual que Task 1
  - Files: `sec_mdna.py`, `tests/test_sec_mdna.py`
- [x] Task 3: HTTP SEC, CIK y `filings`
  - Acceptance: User-Agent y gzip, mapa en caché con refresco ante ausencia, sin `/A`, más reciente primero.
  - Verify: igual que Task 1
  - Files: `sec_mdna.py`, `tests/test_sec_mdna.py`
- [x] Task 4: `fetch_mdna` + caché en disco
  - Acceptance: (actual, anterior | None), sha256, segunda llamada sólo `submissions`.
  - Verify: igual que Task 1
  - Files: `sec_mdna.py`, `tests/test_sec_mdna.py`
- [x] Task 5: CLI + verificación real + commit
  - Acceptance: AAPL 10-Q/10-K, MSFT y NVDA extraen el MD&A del cuerpo; la segunda ejecución hace 1 petición por ticker.
  - Verify: `.venv\Scripts\python -m sec_mdna AAPL 10-Q`

## analyst

- [x] Task 1: `prune_mdna` + medición real
  - Acceptance: reglas de la spec; ninguna línea con `$` perdida; % de reducción en MSFT/AAPL.
  - Verify: `.venv\Scripts\python -m unittest tests.test_analyst -v`
  - Files: `analyst.py`, `tests/test_analyst.py`
- [x] Task 2: `quote_found`
  - Acceptance: tolera tipografía/espacios/mayúsculas; rechaza inventadas, parafraseadas, < 20 caracteres, fuente sin texto.
  - Verify: igual que Task 1
- [x] Task 3: petición y reintentos
  - Acceptance: cabeceras y cuerpo de la spec; 429/529 con `retry-after`; 401 sin reintento ni clave en el error.
  - Verify: igual que Task 1
- [x] Task 4: `parse_response` + `summarize_mdna`
  - Acceptance: refusal/max_tokens/JSON inválido; sólo citas verificadas; máx. 3 por categoría; tokens de `usage`.
  - Verify: igual que Task 1
- [x] Task 5: `render`
  - Acceptance: ≤ 10 líneas, alternancia, línea de descartadas, mensaje sin cambios.
  - Verify: igual que Task 1
- [x] Task 6: CLI + verificación real + commit
  - Acceptance: AAPL 10-Q: JSON válido, ≤ 10 líneas, ≤ 20 % citas rechazadas, < $0,15.
  - Verify: `.venv\Scripts\python -m analyst cache/sec/AAPL_10-Q_2026-06-27.txt cache/sec/AAPL_10-Q_2026-03-28.txt`
