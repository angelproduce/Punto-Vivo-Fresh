"""Lee páginas web para sacar de ahí la información del guion.

Solo se aceptan enlaces http/https públicos. Cada página se descarga con
límite de tamaño y de tiempo, se limpia el HTML y se devuelve texto plano.
No se ejecuta nada de la página: es solo lectura.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from .util import info, warn

MAX_BYTES = 2_500_000          # 2,5 MB por página: suficiente para un artículo
MAX_CHARS = 7_000              # texto útil que se guarda de cada fuente
MAX_SOURCES = 5                # enlaces por vídeo
TIMEOUT = 15.0
MAX_REPORT_CHARS = 14_000      # tope total que se envía a la IA

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HTTP_ERRORS = {
    401: "La web pide iniciar sesión (401).",
    403: "La web bloquea la lectura automática (403).",
    404: "El enlace ya no existe (404).",
    410: "El enlace ya no existe (410).",
    429: "La web limita las visitas (429). Prueba más tarde.",
    500: "La web falló al responder (500).",
    502: "La web falló al responder (502).",
    503: "La web no está disponible ahora (503).",
}
_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "iframe", "form",
    "nav", "footer", "header", "aside", "template", "table", "button",
    "select", "option", "dialog",
}
_BREAK_TAGS = {
    "p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dd", "dt",
    "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "blockquote", "pre",
    "br", "hr", "figure", "figcaption",
}
_VOID_TAGS = {
    "br", "hr", "img", "meta", "link", "input", "source", "track", "area",
    "base", "col", "embed", "param", "wbr",
}
_NOISE_WORDS = (
    "cookie", "consent", "sidebar", "side-bar", "menu", "navbar", "navigation",
    "breadcrumb", "share", "social", "related", "recommend", "comment",
    "newsletter", "subscribe", "advert", "banner", "popup", "modal",
    "infobox", "navbox", "hatnote", "ambox", "metadata", "toc", "reflist",
    "references", "catlinks", "printfooter", "mw-editsection", "noprint",
    "coordinates", "reference",
)
_ALLOWED_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


@dataclass
class Source:
    """Resultado de leer un enlace: texto útil o el motivo del fallo."""
    url: str
    title: str = ""
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


def normalize_url(raw: str) -> str:
    """Devuelve una URL http(s) limpia o lanza ValueError con un motivo claro."""
    text = str(raw or "").strip().strip("<>\"' ")
    if not text:
        raise ValueError("El enlace está vacío.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Solo puedo abrir enlaces http o https.")
    host = (parsed.hostname or "").strip()
    if not host:
        raise ValueError("Ese enlace no tiene un dominio válido.")
    if _is_private_host(host):
        raise ValueError("Ese enlace apunta a tu red local; usa una web pública.")
    return urlunparse(parsed._replace(fragment=""))


def _is_private_host(host: str) -> bool:
    name = host.lower().strip("[]")
    if name in {"localhost", "localhost.localdomain"} or name.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(name)
        return _blocked_ip(address)
    except ValueError:
        pass
    try:
        for item in socket.getaddrinfo(name, None):
            address = ipaddress.ip_address(item[4][0])
            if _blocked_ip(address):
                return True
    except (socket.gaierror, ValueError):
        # Si no se puede resolver, que falle la descarga con su propio error.
        pass
    return False


def _blocked_ip(address) -> bool:
    return (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified)


def _decode(raw: bytes, charset: str | None) -> str:
    if not charset:
        match = re.search(rb"charset=[\"']?([\w-]+)", raw[:5000], re.I)
        if match:
            charset = match.group(1).decode("ascii", "ignore")
    for name in (charset, "utf-8", "latin-1"):
        if not name:
            continue
        try:
            return raw.decode(name, "replace")
        except (LookupError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


_MAIN_PATTERNS = (
    r"(?is)<article\b[^>]*>(.*?)</article\s*>",
    r"(?is)<main\b[^>]*>(.*?)</main\s*>",
    r"(?is)<div[^>]+id=[\"']mw-content-text[\"'][^>]*>(.*)",
)


def _main_html(html: str) -> str:
    """Si hay un artículo o contenido principal, se queda solo con él."""
    for pattern in _MAIN_PATTERNS:
        match = re.search(pattern, html)
        if match and len(match.group(1)) > 800:
            return match.group(1)
    return html


class _TextExtractor(HTMLParser):
    """Convierte HTML en texto descartando menús, anuncios y plantillas.

    Se usa un parser de verdad (no expresiones regulares) porque algunos
    atributos, como los `data-mw` de Wikipedia, llevan JSON con `>` dentro y
    un `<[^>]+>` ingenuo dejaría ese JSON como si fuera texto del artículo.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip: list[str] = []

    @staticmethod
    def _is_noise(attrs: list[tuple[str, str | None]]) -> bool:
        values = " ".join(
            str(value or "") for key, value in attrs
            if key in {"class", "id", "role", "aria-label"}
        ).lower()
        return any(word in values for word in _NOISE_WORDS)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._skip:
            if tag not in _VOID_TAGS:
                self._skip.append(tag)
            return
        if tag in _SKIP_TAGS or self._is_noise(attrs):
            self._skip = [tag]
            return
        if tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip and tag.lower() in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip:
            if tag in self._skip:
                while self._skip:
                    if self._skip.pop() == tag:
                        break
            return
        if tag in _BREAK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _tidy(text: str) -> str:
    text = unescape(text).replace("\xa0", " ")
    # Marcas de referencia y avisos de mantenimiento de los wikis.
    text = re.sub(r"\[\s*\d+\s*\]", "", text)
    text = re.sub(r"(?i)\[\s*(?:cita requerida|editar|editar código|fuente)\s*\]", "", text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    lines = [line.strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _clean_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
        return _tidy(parser.text())
    except Exception:
        # HTML roto: se quitan las etiquetas a lo bruto antes de rendirse.
        return _tidy(unescape(re.sub(r"(?is)<[^>]+>", " ", html)))


def _clean_title(value: str) -> str:
    value = _clean_text(value)
    value = " ".join(value.split())
    # «Titular | Nombre del sitio» → «Titular»
    parts = re.split(r"\s+[|·•]\s+|\s+[–—-]\s+", value)
    if len(parts) > 1 and len(parts[0]) >= 12:
        value = parts[0]
    return value[:180].strip()


def _title_from_html(html: str) -> str:
    for pattern in (r"(?is)<title[^>]*>(.*?)</title>",
                    r"(?is)<h1[^>]*>(.*?)</h1>"):
        match = re.search(pattern, html)
        if match:
            title = _clean_title(match.group(1))
            if title:
                return title
    return ""


def fetch(raw_url: str, timeout: float = TIMEOUT) -> Source:
    """Descarga y limpia una página. Nunca lanza: devuelve Source con error."""
    try:
        url = normalize_url(raw_url)
    except ValueError as exc:
        return Source(url=str(raw_url or "").strip(), error=str(exc))

    request = Request(url, headers={
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = (response.headers.get_content_type() or "").lower()
            charset = response.headers.get_content_charset()
            final_url = response.geturl()
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        message = _HTTP_ERRORS.get(exc.code, f"La web respondió con error {exc.code}.")
        return Source(url=url, error=message)
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return Source(url=url, error="La web tardó demasiado en responder.")
        return Source(url=url, error=f"No pude conectar con la web ({str(reason)[:80]}).")
    except (TimeoutError, socket.timeout):
        return Source(url=url, error="La web tardó demasiado en responder.")
    except Exception as exc:  # red de seguridad: un enlace roto no corta el vídeo
        return Source(url=url, error=f"No pude leer el enlace ({str(exc)[:80]}).")

    if raw is None or not raw.strip():
        return Source(url=url, error="La página llegó vacía.")
    if len(raw) > MAX_BYTES:
        raw = raw[:MAX_BYTES]

    if content_type and content_type not in _ALLOWED_TYPES:
        return Source(url=url, error=f"El enlace no es una página de texto ({content_type}).")

    decoded = _decode(raw, charset)
    is_html = content_type in {"text/html", "application/xhtml+xml"} or "<html" in decoded[:2000].lower()
    if is_html:
        title = _title_from_html(decoded)
        body = _clean_text(_main_html(decoded))
    else:
        title = ""
        body = decoded.strip()

    if len(body) < 60:
        return Source(
            url=final_url or url,
            title=title,
            error="No encontré texto legible (la web puede cargarse con JavaScript o pedir suscripción).",
        )

    cut = False
    if len(body) > MAX_CHARS:
        cut_at = body.rfind(" ", 0, MAX_CHARS)
        body = body[:cut_at if cut_at > MAX_CHARS * 0.75 else MAX_CHARS].rstrip()
        cut = True
    if cut:
        body += " […texto recortado]"
    return Source(url=final_url or url, title=title, text=body)


def fetch_all(urls: list[str] | tuple[str, ...] | None) -> list[Source]:
    """Lee hasta MAX_SOURCES enlaces, sin repetir, y avisa de cada fallo."""
    out: list[Source] = []
    seen: set[str] = set()
    for raw in list(urls or [])[:MAX_SOURCES]:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        info(f"Leyendo fuente: {text[:90]}…")
        source = fetch(text)
        if source.error:
            warn(f"Fuente descartada ({source.error})")
        else:
            info(f"Fuente lista: {source.title or source.url} · {len(source.text)} caracteres")
        out.append(source)
    return out


def usable(sources: list[Source] | None) -> list[Source]:
    return [s for s in (sources or []) if s.ok]


def error_summary(sources: list[Source] | None) -> str:
    lines = []
    for source in sources or []:
        if source.error:
            lines.append(f"{source.url or 'enlace'}: {source.error}")
    return " ".join(lines[:3])


def report_block(sources: list[Source] | None) -> str:
    """Texto para el prompt de la IA, con las fuentes del usuario."""
    blocks: list[str] = []
    total = 0
    for index, source in enumerate(usable(sources), 1):
        header = f"[Fuente {index}] {source.title or '(sin título)'} — {source.url}"
        room = max(500, MAX_REPORT_CHARS - total - len(header))
        body = source.text[:room]
        block = f"{header}\n{body}"
        blocks.append(block)
        total += len(block)
        if total >= MAX_REPORT_CHARS:
            break
    return "\n\n".join(blocks)
