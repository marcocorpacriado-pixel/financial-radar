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
