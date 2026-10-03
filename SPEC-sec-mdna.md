# Spec: sec-mdna

Estado: **APROBADO** (2026-10-03)
Módulo hoja, sin dependencias. Convenciones comunes en `CAPABILITY_MAP.md`.

## Objetivo

Entregar a `analyst` el MD&A **limpio** del último 10-K/10-Q de un ticker y el del filing inmediatamente anterior del mismo tipo, para que pueda redactar qué ha cambiado en el discurso de la dirección. Además, `radar` necesita saber cuál es el último filing (accession + fecha) para detectar publicaciones nuevas. Sólo se extrae el Item 7 (10-K) o el Item 2 de la Parte I (10-Q); el resto del documento es ruido para el LLM y coste en tokens.

## Interfaz pública

```python
@dataclass(frozen=True)
class Filing:
    symbol: str
    cik: str            # 10 dígitos, con ceros a la izquierda
    form: str           # "10-K" | "10-Q"
    accession: str      # "0000320193-26-000020"
    filing_date: str    # "2026-07-31"
    report_date: str    # periodo cubierto: "2026-06-27"
    url: str            # documento principal en /Archives

@dataclass(frozen=True)
class MDNAContext:
    filing: Filing
    text: str           # MD&A limpio
    sha256: str         # hash de text

def filings(symbol: str, form_type: str) -> list[Filing]:
    """Filings 10-K o 10-Q del ticker, más reciente primero (sin enmiendas /A)."""

def fetch_mdna(symbol: str, form_type: str = "10-Q") -> tuple[MDNAContext, MDNAContext | None]:
    """MD&A del último filing de form_type y del inmediatamente anterior del mismo tipo."""
```

- `filings` cuesta 1 petición (`submissions`). `radar` la usa para detectar filings nuevos comparando `accession` con `state.json`, sin descargar documentos.
- El anterior es `None` si no existe (empresa recién cotizada) o si su MD&A no se puede extraer: la alerta del filing actual no se bloquea por el histórico.
- CLI de verificación: `python -m sec_mdna AAPL 10-Q` imprime los metadatos, la longitud y el principio y final del texto de ambos.

## Fuentes EDGAR (verificadas el 2026-10-03)

| Recurso | URL | Uso |
|---|---|---|
| Mapa ticker→CIK | `https://www.sec.gov/files/company_tickers.json` | `{"0": {"cik_str": 320193, "ticker": "AAPL", …}}` |
| Submissions | `https://data.sec.gov/submissions/CIK{cik:010d}.json` | `filings.recent`: arrays paralelos `form`, `accessionNumber`, `filingDate`, `reportDate`, `primaryDocument` (más reciente primero) |
| Documento | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession sin guiones}/{primaryDocument}` | HTML inline XBRL (~1–1,5 MB) |

**Cumplimiento SEC**: toda petición lleva `User-Agent: Marco Corpa marcocorpacriado@gmail.com` y `Accept-Encoding: gzip` (se descomprime con `gzip` de la stdlib). Pausa de 0,15 s antes de cada petición, por debajo del límite de 10 peticiones/s. Timeout 30 s.

## Extracción

**1. HTML → texto** (`html.parser.HTMLParser`, viable: verificado sobre los 10-Q/10-K de AAPL):
- Se descarta el contenido de `script`, `style`, `head`, `ix:header` (hechos XBRL ocultos) y de cualquier elemento con `display:none` (incluidos sus hijos).
- Fuera de tablas, las etiquetas de bloque (`p`, `div`, `br`, `li`, `h1`–`h6`, `tr`) generan salto de línea. Dentro de una celda generan espacio, para que "June 27,<br>2026" no se convierta en "June 27,2026".
- Tablas: una fila por línea, celdas no vacías unidas con ` | `. Se fusionan las celdas que EDGAR parte: `$` se une a la cifra siguiente (`$78,678`), y `)` y `%` a la anterior (`(1,234)`, `12%`).
- Entidades decodificadas (`&#160;` → espacio, `&#8217;` → `’`), espacios colapsados y líneas vacías eliminadas.

**2. Localizar la sección** sobre el texto, línea a línea:

| Form | Inicio (al principio de línea, sin distinguir mayúsculas) | Fin (el primero que aparezca después) |
|---|---|---|
| 10-K | `Item 7.` + `Management's Discussion` (no `7A`) | `Item 7A` o `Item 8` |
| 10-Q | `Item 2.` + `Management's Discussion` | `Item 3`, `Item 4` o `PART II` |

- Exigir "Management's Discussion" en el encabezado descarta el Item 2 de la Parte II del 10-Q (Unregistered Sales) y las referencias cruzadas en mitad de un párrafo. Se aceptan `'`, `’` y separadores ` | . : - –` entre el número y el título.
- **Índice**: el encabezado aparece dos veces (índice y cuerpo). Se evalúan todos los candidatos de inicio y se elige el que produce **la sección más larga** hasta su marcador de fin. La entrada del índice da una sección de una o dos líneas y la del cuerpo da decenas de miles de caracteres. Esta regla no depende del formato concreto del índice.
- Si la mejor sección mide menos de `MIN_CHARS = 2000`, se lanza `ValueError`, con la URL en el mensaje. Es el caso de un MD&A incorporado por referencia a un anexo (Exhibit 13), que queda fuera de alcance.

## Caché en disco

- `cache/sec/{TICKER}_{form}_{report_date}.txt` guarda el MD&A ya limpio. Si existe, no se descarga el documento. El hash se recalcula del contenido.
- `cache/sec/company_tickers.json` se descarga una vez y sólo se vuelve a descargar si un ticker no aparece (altas recientes).
- `submissions` **no** se cachea: es la única forma de ver un filing nuevo. Coste por ticker: 1 petición con la caché llena y 3 en el peor caso.
- `cache/` se añade a `.gitignore`.

## Errores

- `form_type` distinto de `10-K`/`10-Q` → `ValueError`.
- Ticker que no está en el mapa de la SEC, incluso tras volver a descargarlo → `ValueError` (p. ej. emisores extranjeros que presentan 20-F, fuera de alcance).
- Ningún filing de ese tipo → `ValueError`.
- HTTP ≠ 2xx → `RuntimeError` con el código. Un 403 se acompaña de "revisa User-Agent / límite de peticiones".
- MD&A actual no localizable → `ValueError`. Si falla el anterior, se devuelve `None` (ver Interfaz).

## Tests (`tests/test_sec_mdna.py`, sin red, caché en un directorio temporal)

- **HTML → texto**: se descartan `script`, `style`, `ix:header` y `display:none` con `div` anidados; saltos de línea de bloque; `<br>` dentro de celda → espacio; filas como `a | b`; fusión de `$`, `)` y `%`; celdas vacías fuera; entidades.
- **Sección**:
  - Índice en tabla + cuerpo → se devuelve el cuerpo.
  - 10-Q: termina en `Item 3`; sin Item 3, en `Item 4`; el Item 2 de la Parte II no se confunde.
  - 10-K: `Item 7` no casa con `Item 7A`; termina en `7A`, o en `Item 8` si no hay 7A.
  - Mayúsculas (`ITEM 7.`) y apóstrofo curvo; referencia cruzada a mitad de línea ignorada; menos de `MIN_CHARS` → `ValueError`.
- **`filings`**: parseo de `submissions` con enmiendas `10-Q/A` excluidas, orden más reciente primero, URL y CIK de 10 dígitos correctos; ticker en minúsculas.
- **CIK**: con el mapa en caché no hay descarga; ticker ausente → se descarga una vez → `ValueError` si sigue sin estar.
- **`fetch_mdna`** con HTTP simulado:
  - Devuelve (actual, anterior) con `sha256` correcto.
  - La segunda llamada no descarga documentos (sólo `submissions`).
  - Todas las peticiones llevan el User-Agent.
  - Un solo filing → anterior `None`.
  - Anterior no extraíble → `None`.

## Criterios de éxito

- [x] `.venv\Scripts\python -m unittest discover -s tests -v` en verde (todo el proyecto).
- [x] `python -m sec_mdna AAPL 10-Q` y `AAPL 10-K`: el texto empieza en el encabezado del MD&A del cuerpo (no en el índice), termina antes de Item 3 / Item 7A y las cifras de las tablas salen legibles (`$78,678`).
- [x] La misma verificación funciona en MSFT y NVDA (otros estilos de maquetación).
- [x] Una segunda ejecución hace 1 sola petición por ticker (`submissions`).

## Fuera de alcance

MD&A incorporado por referencia a anexos (Exhibit 13), 20-F/40-F de emisores extranjeros, enmiendas (10-K/A, 10-Q/A), filings anteriores a la ventana `recent` de submissions, filings sólo en texto plano (anteriores a ~2001), eliminar cabeceras y pies de página repetidos (ruido menor que el LLM ignora), y caducidad de la caché (los filings no cambian).

## Decisiones

1. Comparación secuencial: 10-Q frente al 10-Q del trimestre anterior (cambio de discurso QoQ); 10-K frente al 10-K del año previo.
2. MD&A completo, sin truncar; el presupuesto de tokens es responsabilidad de `analyst`.
