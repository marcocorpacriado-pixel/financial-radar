# Spec: news

Estado: **APROBADO** (2026-10-03, petición explícita: redactar e implementar en la misma iteración)
Módulo hoja, sin dependencias. Convenciones comunes en `CAPABILITY_MAP.md`.

## Objetivo

Aportar al resumen periódico de `radar` las noticias relevantes de los últimos 14 días de cada posición, también las que no reportan a la SEC (BABA, NXT), a coste cero de datos. `news` sólo obtiene, filtra y deduplica titulares. Elegir los materiales y sintetizarlos es tarea de `analyst.summarize_news` (Haiku), coordinada por `radar`.

## Fuente (verificada el 2026-10-03)

`https://news.google.com/rss/search?q={TICKER}+stock+when:14d&hl=en-US&gl=US&ceid=US:en`

Con `news_query`/`news_lang` en la posición (añadido 2026-10-06, NXT): `q={news_query}+when:14d` y la edición de `LOCALES[news_lang]`; en español `hl=es&gl=ES&ceid=ES:es`. Para NXT (`Nextil OR "Nueva Expresion Textil"`) devuelve prensa española (El Economista, Expansión, Bolsamania…). La deduplicación tokeniza con `\w`, así que las palabras con tilde o ñ cuentan enteras.

- RSS 2.0. Cada `<item>` tiene `title`, `link`, `guid`, `pubDate`, `description` y `source`.
- El título termina en ` - {source}` (`Has MSFT Stock Run Out Of Steam? - trefis.com`).
- `link` es una redirección de Google News de unos 250 caracteres.
- El orden es **el de relevancia de Google**, no cronológico. MSFT devuelve 100 items y ESEA 13.
- Hay mucho relleno SEO ("If You Invest $10,000…", "Should You Buy ESEA?") y algún titular de otra empresa (EDRY en la búsqueda de ESEA). Por eso el filtrado de materialidad lo hace el LLM y no el orden del feed.
- Petición con `User-Agent: financial-radar/1.0 (personal RSS reader)`, timeout 20 s y lectura limitada a 2 MB.

## Interfaz pública

```python
@dataclass(frozen=True)
class Headline:
    title: str              # sin el sufijo " - {source}"
    source: str
    published: datetime     # aware, UTC
    link: str

LOCALES = {"en": "hl=en-US&gl=US&ceid=US:en", "es": "hl=es&gl=ES&ceid=ES:es"}
def feed_url(ticker: str, days: int = 14, query: str | None = None, lang: str = "en") -> str: ...
def fetch_news(ticker, limit=10, now=None, query=None, lang="en") -> list[Headline]: ...
def parse_feed(xml: bytes, now: datetime, days: int = 14) -> list[Headline]:
    """Items válidos dentro de la ventana, en el orden del feed (relevancia de Google)."""
def dedupe(headlines: list[Headline], threshold: float = 0.5) -> list[Headline]:
    """Quita titulares casi idénticos; conserva el primero (el más relevante)."""
def fetch_news(ticker: str, limit: int = 10, now: datetime | None = None) -> list[Headline]:
    """Hasta limit titulares deduplicados de los últimos 14 días."""
```

## Reglas (funciones puras, todas con test)

- **`parse_feed`:**
  - `pubDate` se lee con `email.utils.parsedate_to_datetime`.
  - Se descarta el item si su fecha es ilegible o queda fuera de `[now - days, now + 1 día]`; el margen absorbe desfases de zona horaria.
  - Se descarta el item sin título o sin enlace.
  - Se quita el sufijo ` - {source}` del título.
- **`dedupe`** (duplicados "semánticos" con herramientas de la stdlib):
  1. Título en minúsculas.
  2. Sin puntuación, guardando `%`, `$` y números.
  3. Sin un puñado de stopwords en inglés y sin palabras genéricas (`stock`, `shares`, `inc`, `corp`).
  4. Se comparan como conjunto de palabras con similitud de Jaccard: ≥ 0,5 cuenta como duplicado.

  Detecta la misma noticia contada por varios medios ("Microsoft gives Copilot a much-needed overhaul, and the stock soars" frente a "Microsoft stock soars after Copilot overhaul"). No detecta paráfrasis sin palabras en común; eso requeriría embeddings o un LLM y queda fuera de alcance.
- **`fetch_news`:** `parse_feed` → `dedupe` → primeros `limit`. Errores HTTP o de red → `RuntimeError` con el ticker; XML inválido → `ValueError`.

**Seguridad:** `xml.etree` sobre un feed de Google. Desde Python 3.7.1, expat incluye protecciones contra la expansión exponencial de entidades ("billion laughs"), y el límite de 2 MB acota el resto.

## Uso desde `radar` (ver `SPEC-radar.md`)

1. Durante el resumen, `fetch_news(ticker, limit=10)` por posición. Son candidatos, no el resultado final.
2. Una única llamada a `analyst.summarize_news` con `summary_model` (Haiku). Recibe los candidatos numerados de todas las posiciones y devuelve, por ticker, una síntesis en español y los índices (≤ 3) de los titulares que la respaldan.
3. En Telegram se muestra la síntesis y los ≤ 3 titulares elegidos (`fuente, fecha: título`). Los enlaces se omiten porque las redirecciones de Google ocupan unos 250 caracteres cada una.
4. Si Haiku falla, se muestran los 3 primeros candidatos sin síntesis. Si falla el feed, `Noticias: no disponibles`.

## Tests (`tests/test_news.py`, fixtures XML, sin red)

- **`parse_feed`:**
  - título sin sufijo;
  - fuente, fecha en UTC y enlace;
  - item antiguo fuera de la ventana;
  - fecha ilegible;
  - item sin título o sin enlace;
  - orden del feed conservado.
- **`dedupe`:** misma noticia en dos medios → una; noticias distintas sobre el mismo ticker → ambas; conserva la primera.
- **`fetch_news`:**
  - URL con `q=MSFT+stock+when:14d`;
  - User-Agent;
  - `limit` aplicado tras deduplicar;
  - HTTP 503 → `RuntimeError`;
  - XML roto → `ValueError`.

## Criterios de éxito

- [x] Tests en verde (todo el proyecto).
- [x] `python -m news MSFT` y `python -m news ESEA` imprimen titulares reales deduplicados de los últimos 14 días.

## Evaluado y descartado: noticias de FMP (2026-10-03, Fase A)

Se valoró usar FMP como fuente principal, con Google News como respaldo. Se descartó tras probarlo con la clave real:

| Prueba | Resultado |
|---|---|
| `api/v3/stock_news` (URL propuesta) | **403**: endpoint legacy, sólo para suscriptores anteriores a la migración a `stable/` |
| `stable/news/stock?symbols=MSFT` | 200, pero el mismo relleno SEO que Google ("Is a Microsoft Stock Split Coming…", 247wallst) |
| `stable/news/press-releases?symbols=MSFT` | 200, pero son **notas de prensa de terceros** que mencionan a Microsoft (IntuigenceAI, Semarchy, Trust3 AI), no de Microsoft |
| `stable/news/stock?symbols=REY.MI` | 0 resultados |
| `stable/news/stock?symbols=ESEA` | La más reciente es del 25/08, fuera de la ventana de 14 días |

FMP no mejoraba la calidad y añadía 2 llamadas por ticker. La mejora real está en el **criterio de selección de Haiku** (ver `SPEC-analyst.md` §5), que se endureció con tres grupos de hechos materiales.

## Fuera de alcance

Otras fuentes (Yahoo, Seeking Alpha), resolver la URL final de la noticia, descargar el cuerpo del artículo, análisis de sentimiento, deduplicación por embeddings e histórico de noticias ya enviadas (el resumen es periódico; repetir un titular de hace 10 días es aceptable).
