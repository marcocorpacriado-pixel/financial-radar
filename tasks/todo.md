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
