"""Guiones: plantillas locales o IA (Gemini, ChatGPT/OpenAI y GitHub Models).

Con `script.provider: auto` se usa el primer proveedor que tenga clave y, si
ninguno responde, se compone un guion largo con los temas y la investigación.
Los vídeos duran entre 60 y 120 segundos (configurable en `config.yaml`).
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import research, sources as web
from .config import env, get, path_of
from .keywords import main_subject as _main_subject
from .keywords import person_subject as _person_subject
from .keywords import scene_query as _visual_query
from .util import STATE, info, read_json, slugify, warn, write_json

USED = STATE / "used_topics.json"

# ~2,8 palabras por segundo en español con la voz clonada.
WORDS_PER_SECOND = 2.8


@dataclass
class Scene:
    """Un tramo del guion con su propia búsqueda visual."""
    narration: str
    visual_query: str
    media_type: str = "auto"   # video | image | auto
    subject: str = ""          # protagonista real: persona, lugar u organización

    @property
    def words(self) -> int:
        return len(re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", self.narration or ""))


@dataclass
class Script:
    topic_id: str
    title: str
    hook: str
    body: str
    cta: str
    hashtags: list[str] = field(default_factory=list)
    description: str = ""
    provider: str = "plantilla"
    related_ids: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    intro_query: str = ""
    # Lo que narra la intro. None = el título; "" = intro en silencio.
    intro_text: str | None = None

    @property
    def full_text(self) -> str:
        if self.scenes:
            spoken = " ".join(s.narration for s in self.scenes if s.narration).strip()
            if spoken:
                return spoken
        return " ".join(part for part in (self.hook, self.body, self.cta) if part).strip()

    @property
    def word_count(self) -> int:
        return len(re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", self.full_text))

    @property
    def est_seconds(self) -> float:
        return self.word_count / WORDS_PER_SECOND


def target_seconds(cfg: dict, seconds: float | None = None) -> float:
    """Duración pedida, siempre entre el mínimo y el máximo (60-120 s)."""
    if seconds is None or float(seconds) <= 0:
        seconds = get(cfg, "project.target_duration", 90)
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 90.0
    low = float(get(cfg, "project.min_duration", 60) or 60)
    high = float(get(cfg, "project.max_duration", 120) or 120)
    return max(low, min(high, value))


def max_seconds(cfg: dict) -> float:
    return float(get(cfg, "project.max_duration", 120) or 120)


def _target_words(seconds: float) -> int:
    return max(60, int(round(seconds * WORDS_PER_SECOND)))


# ------------------------------------------------------------------ temas
def _load_topics(cfg: dict) -> list[dict]:
    path = path_of(get(cfg, "script.topics_file", "topics.yaml"))
    if not path.exists():
        raise FileNotFoundError(f"Falta {path.name}. Reinstala el proyecto.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    topics = [t for t in (data.get("topics") or []) if isinstance(t, dict) and t.get("id")]
    return topics


def used_topics() -> list[str]:
    data = read_json(USED, []) or []
    return [str(x) for x in data]


def mark_used(topic_id: str) -> None:
    used = used_topics()
    if topic_id not in used:
        used.append(topic_id)
    write_json(USED, used)


def reset_used() -> None:
    write_json(USED, [])


def _topic_text(topic: dict) -> str:
    body = str(topic.get("body") or "").strip()
    if not body:
        facts = topic.get("facts") or []
        body = " ".join(str(f).strip() for f in facts)
    return body


def _trim_words(text: str, limit: int) -> str:
    """Recorta un texto al presupuesto de palabras sin partir una frase.

    Una fuente larga (un artículo entero) no puede convertirse en un vídeo de
    6 minutos cuando se pidieron 90 segundos: se queda con las primeras frases
    completas y se corta ahí.
    """
    words = str(text or "").split()
    if limit <= 0:
        return ""
    if len(words) <= limit:
        return " ".join(words)
    cut = " ".join(words[:limit])
    floor = max(1, int(limit * 0.60))
    for match in reversed(list(re.finditer(r"[.!?…](?=\s|$)", cut))):
        sentence = cut[:match.end()].strip()
        if len(sentence.split()) >= floor:
            return sentence
    comma = cut.rfind(",")
    if comma > len(cut) * 0.60:
        return cut[:comma].rstrip(",;:") + "."
    return cut.rstrip(",;:") + "."


def _split_scenes(text: str, title: str, max_scenes: int = 10) -> list[Scene]:
    """Divide un texto en escenas y crea una consulta visual por escena.

    Es el respaldo cuando no hay IA: nunca usa una sola imagen para todo el
    vídeo ni una consulta genérica repetida.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", str(text or "").strip())
                 if len(s.strip()) > 8]
    if not sentences:
        sentences = [str(title or text or "Actualidad").strip()]
    if len(sentences) > max_scenes:
        chunk = max(1, math.ceil(len(sentences) / max_scenes))
        sentences = [" ".join(sentences[i:i + chunk]).strip()
                     for i in range(0, len(sentences), chunk)][:max_scenes]

    def visual_of(sentence: str) -> str:
        # «Según El Sur Acapulco..., ...»: la fuente no describe la imagen.
        clean = re.sub(r"^[Ss]egún\s+[^,]{1,80},\s*", "", sentence).strip()
        return _visual_query(clean or sentence, title)

    return [
        Scene(narration=sentence,
              visual_query=visual_of(sentence),
              media_type="auto",
              subject=_main_subject(sentence))
        for sentence in sentences
    ]


def _compose(topic: dict) -> Script:
    title = str(topic.get("title") or "Sin título").strip()
    hook = str(topic.get("hook") or "").strip()
    body = _topic_text(topic)
    if not hook:
        hook = f"{title}."
    cta = str(topic.get("cta") or "Sígueme para más.").strip()
    hashtags = [str(h).lstrip("#") for h in (topic.get("hashtags") or [])]
    description = str(topic.get("description") or hook).strip()
    scenes = _split_scenes(" ".join(part for part in (hook, body, cta) if part), title, 8)
    return Script(
        topic_id=str(topic["id"]),
        title=title,
        hook=hook,
        body=body,
        cta=cta,
        hashtags=hashtags or ["shorts", "tecnologia"],
        description=description,
        scenes=scenes,
        intro_query=_visual_query(title, title),
    )


def _ordered_topics(topics: list[dict], first: dict) -> list[dict]:
    """El tema elegido primero y después los de la rotación."""
    rest = [t for t in topics if str(t["id"]) != str(first["id"])]
    used = set(used_topics())
    rest.sort(key=lambda t: str(t["id"]) in used)
    return [first] + rest


def _sentence_case(title: str) -> str:
    """«Los elogios…» → «los elogios…» para que encaje tras «Según X,»."""
    first, _, rest = title.partition(" ")
    if first in {"El", "La", "Los", "Las", "Un", "Una", "Unos", "Unas"}:
        return f"{first.lower()} {rest}".strip()
    return title


def _compose_long(cfg: dict, topics: list[dict], topic: dict, seconds: float,
                  items: list[dict]) -> Script:
    """Guion largo sin IA: une el tema con la investigación y otros temas."""
    base = _compose(topic)
    goal = _target_words(seconds)
    limit = int(goal * 1.12)
    pieces: list[str] = [base.hook]
    # El texto base se recorta al presupuesto restante: si el tema viene de un
    # artículo largo, el vídeo sigue durando lo pedido y termina en frase.
    budget = max(0, limit - len(base.hook.split()))
    step = _trim_words(_topic_text(topic), budget)
    if step:
        pieces.append(step)

    def words() -> int:
        return sum(len(piece.split()) for piece in pieces)

    sources: list[str] = []
    for item in items:
        title = str(item.get("title") or "").strip().rstrip(".")
        if len(title) < 18:
            continue
        source = str(item.get("source") or "").strip()
        line = f"Según {source or 'la actualidad'}, {_sentence_case(title)}."
        if words() + len(line.split()) > limit:
            break
        if source and source not in sources:
            sources.append(source)
        pieces.append(line)
        if words() >= goal:
            break

    related: list[str] = []
    if words() < goal * 0.9:
        bridges = ("Y hay más.", "Además.", "Y ojo con esto.", "Otro detalle.")
        for index, extra in enumerate(_ordered_topics(topics, topic)[1:], 1):
            text = _topic_text(extra)
            if not text:
                continue
            pair = [bridges[index % len(bridges)], f"{extra.get('title')}. {text}"]
            size = sum(len(piece.split()) for piece in pair)
            if related and words() + size > limit:
                break
            pieces.extend(pair)
            related.append(str(extra["id"]))
            if words() >= goal:
                break

    body = " ".join(pieces[1:]).strip()
    scenes = _split_scenes(" ".join(pieces), base.title, 10)
    return Script(
        topic_id=str(topic["id"]),
        title=base.title,
        hook=base.hook,
        body=body,
        cta=base.cta,
        hashtags=base.hashtags,
        description=base.description,
        provider="plantilla",
        related_ids=related,
        sources=sources,
        scenes=scenes,
        intro_query=_visual_query(base.title, base.title),
    )


# ------------------------------------------------------------------ guion manual / fuentes
_TITLE_LINE_RE = re.compile(
    r"^\s*(?:#{1,3}\s+|(?:t[ií]tulo|title)\s*[:：\-]\s*)(.+)$", re.I)

_KNOWN_TAGS: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("gta 6", "gta6", "grand theft auto"), ["GTA6", "GrandTheftAuto", "RockstarGames", "Gaming"]),
    (("alofoke",), ["Alofoke", "CasaDeAlofoke"]),
    (("playstation", "ps5"), ["PS5", "PlayStation"]),
    (("xbox",), ["Xbox"]),
    (("nintendo", "switch"), ["Nintendo", "Switch"]),
    (("minecraft",), ["Minecraft"]),
    (("fortnite",), ["Fortnite"]),
)


def _manual_sentences(text: str) -> list[str]:
    """Divide un guion escrito a mano por líneas y por signos de puntuación."""
    parts: list[str] = []
    for block in re.split(r"\n\s*\n+", str(text or "")):
        for line in block.splitlines():
            line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line).strip()
            if not line:
                continue
            for piece in re.split(r"(?<=[.!?…])\s+", line):
                piece = piece.strip()
                if len(piece) >= 3:
                    parts.append(piece)
    return parts


def _short_title(text: str, limit: int = 70) -> str:
    value = " ".join(str(text or "").split()).strip().strip('"“”')
    if len(value) <= limit:
        return value
    cut = value[:limit].rsplit(" ", 1)[0].rstrip(",;:.-")
    return (cut or value[:limit]).strip() + "…"


def _manual_hashtags(text: str) -> list[str]:
    low = str(text or "").lower()
    tags = ["shorts", "actualidad"]
    for keys, values in _KNOWN_TAGS:
        if any(key in low for key in keys):
            tags.extend(values)
    clean: list[str] = []
    for tag in tags:
        tag = tag.lstrip("#").strip()
        if tag and tag not in clean:
            clean.append(tag)
    return clean[:10]


def _chunk_long(sentence: str, limit: int = 32) -> list[str]:
    """Parte una frase muy larga para que cada escena tenga un plano propio."""
    words = sentence.split()
    if len(words) <= limit:
        return [sentence]
    pieces = [p.strip() for p in re.split(r"(?<=[,;:])\s+", sentence) if p.strip()]
    out: list[str] = []
    current = ""
    for piece in pieces:
        if current and len((current + " " + piece).split()) > limit:
            out.append(current)
            current = piece
        else:
            current = (current + " " + piece).strip()
    if current:
        out.append(current)
    final: list[str] = []
    for piece in out:
        words_piece = piece.split()
        while len(words_piece) > limit * 1.35:
            final.append(" ".join(words_piece[:limit]))
            words_piece = words_piece[limit:]
        if words_piece:
            final.append(" ".join(words_piece))
    return final or [sentence]


def _script_from_manual(cfg: dict, text: str, seconds: float,
                        sources: list[web.Source] | None = None) -> Script:
    """Guion escrito por el usuario: manda sobre el tema y sobre las fuentes."""
    raw = str(text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        raise ValueError("El guion manual está vacío.")

    lines = [line.strip() for line in raw.splitlines()]
    first_line = next((line for line in lines if line), "")
    title_hint = ""
    match = _TITLE_LINE_RE.match(first_line) if first_line else None
    if match:
        title_hint = match.group(1).strip()
        body_lines = []
        skipped = False
        for line in lines:
            if not skipped and line == first_line:
                skipped = True
                continue
            body_lines.append(line)
        body_text = "\n".join(body_lines).strip()
    else:
        body_text = raw

    sentences: list[str] = []
    for sentence in _manual_sentences(body_text):
        sentences.extend(_chunk_long(sentence))
    if not sentences and title_hint:
        sentences = [_chunk_long(title_hint)[0]]

    if title_hint:
        title = _short_title(title_hint, 80)
        intro_text: str | None = title_hint
        narration = " ".join(sentences)
        if not narration:
            intro_text = ""
            narration = title_hint
    else:
        intro_text = sentences[0]
        title = _short_title(intro_text, 70)
        narration = " ".join(sentences[1:])

    if not narration:
        # Guion de una sola frase: se narra una vez y la intro queda en silencio.
        intro_text = ""
        narration = sentences[0]

    scenes = _split_scenes(narration, title, 10) if narration else []
    hook = (intro_text or sentences[0]).strip()
    description = " ".join(sentences[:2])[:240] or title
    estimated = sum(len(s.narration.split()) for s in scenes) / WORDS_PER_SECOND
    if abs(estimated - seconds) > max(12.0, seconds * 0.30):
        warn(f"El guion manual dura ~{estimated:.0f} s y pediste {seconds:.0f} s; "
             "se respeta tu texto.")
    info(f"Guion manual: {len(scenes)} escenas · {sum(len(s.narration.split()) for s in scenes)} palabras.")
    return Script(
        topic_id=slugify(title, 40) or "guion-manual",
        title=title,
        hook=hook,
        body=" ".join(s.narration for s in scenes).strip() or narration,
        cta="",
        hashtags=_manual_hashtags(title + " " + narration),
        description=description,
        provider="manual",
        sources=[s.url for s in web.usable(sources)],
        scenes=scenes,
        intro_query=_visual_query(title, title),
        intro_text=intro_text,
    )


def _topic_from_sources(usable: list[web.Source]) -> dict:
    """Convierte las páginas leídas en el tema del vídeo."""
    primary = usable[0]
    title = (primary.title or "").strip()
    if len(title) < 12:
        sentences = _manual_sentences(primary.text)
        title = sentences[0] if sentences else "Fuentes web"
    title = _short_title(title, 80)
    body = "\n\n".join(source.text for source in usable)[:12_000]
    return {
        "id": slugify(title, 40) or "fuentes-web",
        "title": title,
        "body": body,
        "hook": title if title.endswith((".", "!", "?", "…")) else title + ".",
        "cta": "",
        "hashtags": _manual_hashtags(title + " " + body[:2000]),
        "description": _short_title(body, 240) or title,
    }



# ------------------------------------------------------------------ IA
def _prompt(cfg: dict, topic: dict, report: str, seconds: float) -> str:
    niche = str(get(cfg, "project.niche", "general") or "general")
    words = _target_words(seconds)
    low, high = int(words * 0.88), int(words * 1.12)
    return (
        "Eres guionista y editor de un canal profesional de noticias y "
        "documentales en español. Escribes para un short vertical 9:16. "
        f"Nicho: {niche}. Tono informativo, natural, cercano y riguroso; "
        "frases cortas, sin muletillas ni relleno.\n"
        f"Escribe la narración de {int(seconds)} segundos: entre {low} y {high} "
        f"palabras (unas {words}). No repitas el título: ya se narra en la intro.\n\n"
        "ESTRUCTURA OBLIGATORIA:\n"
        "- Los primeros 4 segundos muestran un fragmento real del acontecimiento "
        "y se narra el título del vídeo; la narración del guion empieza después.\n"
        "- Divide la narración en 6-10 escenas. Cada escena: 1-2 frases "
        "(15-30 palabras), con un cambio visual claro.\n"
        "- La última escena cierra con una invitación breve y natural.\n\n"
        "REGLAS DE LOS RECURSOS VISUALES:\n"
        "- Cada escena debe llevar 'subject' en INGLÉS: el protagonista real "
        "de esa escena (la persona, el lugar o la organización concreta). Si "
        "la escena habla de Donald Trump, el subject es 'Donald Trump'; si "
        "habla del Monte Vesubio, 'Mount Vesuvius'. Si hay una persona "
        "nombrada que actúa o habla, el subject es esa persona, no el "
        "edificio ni el país donde está.\n"
        "- Cada escena debe llevar 'visual_query' en INGLÉS, de 3 a 7 palabras, "
        "literal y concreta. Debe describir lo que se ve en la noticia y "
        "nombrar el mismo protagonista que 'subject' cuando sea una persona "
        "o un lugar reconocible (p. ej. 'Donald Trump press conference' o "
        "'Mount Vesuvius eruption smoke').\n"
        "- Ejemplos: guerra → 'war soldiers rubble city'; terremoto → "
        "'earthquake collapsed buildings rescue'; tecnología → "
        "'artificial intelligence robot laboratory'; famoso → "
        "'singer concert stage close up'.\n"
        "- Prohibido: 'abstract', 'background', 'gradient', 'wallpaper', "
        "'texture', 'city', 'office', 'people' o cualquier término genérico. "
        "Si la escena habla de un lugar o hecho, nómbralo.\n"
        "- Prohibido también: frescos, mosaicos, pinturas, esculturas, relieves, "
        "objetos de museo, grabados, mapas, gráficos o documentos. Usa el lugar, "
        "las personas o la acción real del acontecimiento.\n"
        "- El cierre también debe mostrar el tema real del vídeo, no arte ni "
        "material de archivo.\n"
        "- No repitas la misma visual_query entre escenas.\n"
        "- 'media_type' debe ser 'video', 'image' o 'auto' según lo que exista "
        "más probablemente: video para acción/acontecimientos; image para "
        "retratos, mapas, productos o datos.\n"
        "- 'intro_query' describe en INGLÉS el fragmento real de 4 segundos "
        "del acontecimiento principal (sin estudios de televisión ni logos). "
        "Si el hecho es histórico y no existen grabaciones, describe una "
        "vista real del lugar (ruinas, ciudad, volcán), no una recreación ni "
        "la erupción de otro volcán.\n\n"
        "RIGOR:\n"
        "- Usa los datos del informe. No inventes cifras, fechas ni citas.\n"
        "- Si el informe trae «FUENTES APORTADAS POR EL USUARIO», son la base "
        "principal: usa solo datos que aparezcan en ellas y no mezcles noticias "
        "de otros temas.\n"
        "- Si un dato no aparece, habla en general sin afirmar cosas falsas.\n"
        "- El texto de 'narration' es exactamente lo que dirá la voz.\n\n"
        f"Tema: {topic.get('title')}\n"
        f"Datos de apoyo: {_topic_text(topic)}\n"
        f"Informe de actualidad:\n{report}\n\n"
        "Devuelve SOLO JSON válido con esta forma exacta:\n"
        '{"title": "titular de máx. 70 caracteres", '
        '"intro_query": "english visual search query", '
        '"scenes": [{"narration": "texto que se narra", '
        '"subject": "english exact person place or organization", '
        '"visual_query": "english visual search query", '
        '"media_type": "video|image|auto"}], '
        '"cta": "frase final", "hashtags": ["6","sin","#"], '
        '"description": "una frase para la publicación"}'
    )


def _parse_json(text: str) -> dict:
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.MULTILINE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("La IA no devolvió JSON.")
    return json.loads(text[start:end + 1])


_ART_QUERY = {
    "fresco", "frescoes", "mosaic", "mosaics", "mural", "murals",
    "painting", "paintings", "statue", "statues", "sculpture", "sculptures",
    "museum", "artifact", "artifacts", "artefact", "manuscript", "engraving",
    "relief", "reliefs", "bas-relief", "basrelief",
    "inscription", "inscriptions", "epigraph", "stele", "plaque", "frieze",
    "print", "prints", "scan", "scans", "etching", "woodcut", "album",
    "vedute", "rijksmuseum",
    "drawing", "sketch", "map", "chart", "diagram", "infographic", "document",
    "book", "coin", "coins", "relic", "relics",
}


def _clean_visual(query: str, title: str) -> str:
    """Quita de la consulta cualquier término de arte, museo o archivo."""
    parts = [word for word in str(query or "").split()
             if word.lower().strip(",.") not in _ART_QUERY]
    cleaned = " ".join(parts).strip()
    if len(parts) >= 3:
        return cleaned
    return _visual_query(title, title) or query


def _scenes_from_data(data: dict, topic: dict, title: str) -> list[Scene]:
    """Convierte la respuesta de la IA en escenas con consulta visual."""
    raw = data.get("scenes")
    scenes: list[Scene] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                narration = str(item.get("narration") or item.get("text") or "").strip()
                query = str(item.get("visual_query") or item.get("query") or "").strip()
                media_type = str(item.get("media_type") or "auto").strip().lower()
                subject = str(item.get("subject") or "").strip()
            else:
                narration = str(item or "").strip()
                query = ""
                media_type = "auto"
                subject = ""
            if len(narration) < 4:
                continue
            if not query:
                query = _visual_query(narration, title)
            query = _clean_visual(query, title)
            # Si la narración nombra a una persona y la IA puso un lugar como
            # sujeto, manda la persona: «Donald Trump» no es «White House».
            named = _main_subject(narration)
            if named and _person_subject(named) and not _person_subject(subject):
                subject = named
            if not subject:
                subject = named
            if media_type not in {"video", "image", "auto"}:
                media_type = "auto"
            scenes.append(Scene(narration=narration, visual_query=query,
                                media_type=media_type, subject=subject))
    if len(scenes) < 2:
        text = " ".join(str(data.get(k) or "").strip()
                        for k in ("hook", "body", "cta")).strip()
        scenes = _split_scenes(text or title, title, 10)
    return scenes


def _script_from(data: dict, topic: dict, provider: str, seconds: float) -> Script:
    base = _compose(topic)
    title = str(data.get("title") or base.title).strip()[:80]
    scenes = _scenes_from_data(data, topic, title)
    hashtags = [str(h).lstrip("#") for h in (data.get("hashtags") or [])]
    spoken = " ".join(s.narration for s in scenes).strip()
    intro = str(data.get("intro_query") or "").strip()
    if intro:
        intro = _clean_visual(intro, title)
    else:
        intro = _visual_query(title, title) or _visual_query(spoken[:120], title)
    script = Script(
        topic_id=str(topic["id"]),
        title=title,
        hook=scenes[0].narration if scenes else str(data.get("hook") or "").strip(),
        body=" ".join(s.narration for s in scenes[1:]).strip()
        or str(data.get("body") or "").strip(),
        cta=str(data.get("cta") or base.cta).strip(),
        hashtags=hashtags or base.hashtags,
        description=str(data.get("description") or base.description).strip(),
        provider=provider,
        scenes=scenes,
        intro_query=intro,
    )
    if not script.hook:
        script.hook = base.hook
    if script.word_count < _target_words(seconds) * 0.55:
        raise ValueError("El guion salió demasiado corto.")
    if len(script.scenes) < 2:
        raise ValueError("La IA no devolvió escenas suficientes.")
    return script


def _http_json(url: str, payload: dict, headers: dict, timeout: float = 60,
               tries: int = 3) -> dict:
    body = json.dumps(payload).encode("utf-8")
    delay = 3.0
    for attempt in range(tries):
        request = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", **headers}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            # 503/500 son caídas puntuales del proveedor; 429 en Gemini es
            # límite de peticiones y sí conviene reintentar. En OpenAI un 429
            # suele ser «sin crédito» y no se arregla esperando.
            retry = exc.code in (500, 502, 503, 504) or (
                exc.code == 429 and "generativelanguage" in url)
            if retry and attempt < tries - 1:
                warn(f"IA saturada ({exc.code}); reintento {attempt + 1}/{tries - 1}…")
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception as exc:
            # Cortes de red o tiempos de espera agotados: el proveedor puede
            # tardar más de lo normal, así que se reintenta la misma petición.
            retry = isinstance(exc, (urllib.error.URLError, TimeoutError, OSError))
            if retry and attempt < tries - 1:
                warn(f"IA sin respuesta ({type(exc).__name__}); "
                     f"reintento {attempt + 1}/{tries - 1}…")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("La IA no respondió tras varios intentos")


def _gemini(cfg: dict, prompt: str) -> str:
    key = env("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Falta GEMINI_API_KEY")
    model = str(get(cfg, "script.gemini_model", "gemini-3.8-flash"))
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":generateContent?key={key}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "responseMimeType": "application/json",
            "maxOutputTokens": 3000,
            # Sin «thinking»: el guion debe salir rápido y no gastar la
            # salida en razonamiento interno.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    data = _http_json(url, payload, {},
                      timeout=float(get(cfg, "script.timeout", 240) or 240))
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _openai(cfg: dict, prompt: str) -> str:
    key = env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Falta OPENAI_API_KEY")
    model = str(get(cfg, "script.openai_model", "gpt-4o-mini"))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": 2400,
        "response_format": {"type": "json_object"},
    }
    data = _http_json(
        "https://api.openai.com/v1/chat/completions", payload,
        {"Authorization": f"Bearer {key}"},
    )
    return data["choices"][0]["message"]["content"]


def _github(cfg: dict, prompt: str) -> str:
    key = env("GITHUB_TOKEN") or env("GITHUB_MODELS_TOKEN")
    if not key:
        raise RuntimeError("Falta GITHUB_TOKEN")
    model = str(get(cfg, "script.github_model", "openai/gpt-4o-mini"))
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": 2400,
        "response_format": {"type": "json_object"},
    }
    data = _http_json(
        "https://models.github.ai/inference/chat/completions", payload,
        {"Authorization": f"Bearer {key}", "Accept": "application/vnd.github+json"},
    )
    return data["choices"][0]["message"]["content"]


_PROVIDERS = {"gemini": _gemini, "openai": _openai, "github": _github}
_LABELS = {"gemini": "Gemini", "openai": "ChatGPT", "github": "GitHub Models"}


def provider_ready(name: str) -> bool:
    if name == "gemini":
        return bool(env("GEMINI_API_KEY"))
    if name == "openai":
        return bool(env("OPENAI_API_KEY"))
    if name == "github":
        return bool(env("GITHUB_TOKEN") or env("GITHUB_MODELS_TOKEN"))
    return False


def provider_order(cfg: dict, include_fallback: bool = True) -> list[str]:
    """Proveedores a probar, en orden.

    Con `provider: auto` se encadena la lista configurada. Si hay uno forzado
    (por ejemplo Gemini), no se cae a otro de pago: `include_fallback=False`
    deja solo ese proveedor y, si falla, el pipeline usa plantillas locales.
    """
    chosen = str(get(cfg, "script.provider", "auto") or "auto").lower()
    order = list(get(cfg, "script.provider_order", ["gemini", "openai", "github"]) or [])
    order = [str(name).lower() for name in order if str(name).lower() in _PROVIDERS]
    if chosen in _PROVIDERS:
        if not include_fallback:
            return [chosen]
        return [chosen] + [name for name in order if name != chosen]
    if not include_fallback and chosen in {"template", "plantilla", "none"}:
        return []
    return order or ["gemini", "openai", "github"]


def _ask(cfg: dict, name: str, topic: dict, report: str, seconds: float) -> Script | None:
    prompt = _prompt(cfg, topic, report, seconds)
    try:
        text = _PROVIDERS[name](cfg, prompt)
        return _script_from(_parse_json(text), topic, name, seconds)
    except Exception as exc:
        message = str(exc)
        retry = any(word in message.lower() for word in ("corto", "json", "decode", "keyerror"))
        warn(f"{_LABELS.get(name, name)} no respondió ({message[:120]}).")
        if not retry:
            return None
    # Solo se reintenta cuando el problema fue el formato o la longitud.
    try:
        text = _PROVIDERS[name](
            cfg, prompt + "\nEl guion anterior salió corto: amplía el body."
        )
        return _script_from(_parse_json(text), topic, name, seconds)
    except Exception as exc:
        warn(f"{_LABELS.get(name, name)} sigue sin responder ({str(exc)[:120]}).")
        return None


# ------------------------------------------------------------------ API
def generate_script(cfg: dict, topic_id: str | None = None,
                    seconds: float | None = None,
                    sources: list[str] | None = None,
                    manual_script: str | None = None) -> Script:
    """Crea el guion. Prioridad: manual > fuentes web > tema > rotación."""
    duration = target_seconds(cfg, seconds)
    read = web.fetch_all(sources)
    usable = web.usable(read)

    if str(manual_script or "").strip():
        if usable:
            info(f"El guion manual manda; uso {len(usable)} fuente(s) solo como referencia.")
        return _script_from_manual(cfg, str(manual_script), duration, read)

    if read and not usable and not str(topic_id or "").strip():
        detail = web.error_summary(read)
        raise RuntimeError(
            "No pude leer ninguna de las fuentes. " + detail +
            " Pega el texto en «Guion manual» o escribe un tema."
        )

    topics = _load_topics(cfg)
    used = set(used_topics())

    topic: dict | None = None
    if usable:
        # Prioridad: el enlace manda sobre el tema escrito. El título y el
        # contenido salen de la propia página, no de la actualidad general.
        topic = _topic_from_sources(usable)
        info(f"Tema tomado de la fuente: {topic['title']}")
        if str(topic_id or "").strip():
            info("Con enlaces, el tema escrito no se usa: manda la fuente.")
    elif topic_id:
        topic = next((t for t in topics if str(t["id"]) == topic_id), None)
        if topic is None:
            # Tema libre escrito por el usuario.
            slug = slugify(topic_id, 40) or "tema-libre"
            topic = {
                "id": slug,
                "title": topic_id.strip(),
                "body": "",
                "hook": topic_id.strip() + ".",
                "cta": "Sígueme para más.",
                "hashtags": ["shorts", "tecnologia"],
                "description": topic_id.strip(),
            }

    if topic is None:
        if not topics:
            raise ValueError("No hay temas guardados. Escribe un tema en la barra de búsqueda.")
        # Rotación infinita: cuando se acaban los temas, se vuelve a empezar.
        free = [t for t in topics if str(t["id"]) not in used]
        if not free:
            reset_used()
            free = topics
        topic = free[0]

    items: list[dict] = []
    report = ""
    if usable:
        # Con fuentes del usuario no se mezcla la actualidad general: el vídeo
        # debe hablar de lo que dice el enlace.
        report = ("FUENTES APORTADAS POR EL USUARIO "
                  "(úsalas como base principal; no inventes):\n\n" + web.report_block(usable))
    else:
        try:
            items = research.gather(cfg, topic)
        except Exception as exc:
            warn(f"Sin investigación externa ({str(exc)[:90]}).")
        report = research.brief(items)

    for name in provider_order(cfg, include_fallback=False):
        if not provider_ready(name):
            continue
        script = _ask(cfg, name, topic, report, duration)
        if script:
            found = {str(item.get("source")) for item in items if item.get("source")}
            found.update(source.url for source in usable)
            script.sources = sorted(found)
            info(f"Guion escrito con {_LABELS.get(name, name)} · {script.word_count} palabras "
                 f"(~{script.est_seconds:.0f} s).")
            return script

    script = _compose_long(cfg, topics, topic, duration, items)
    if usable:
        script.sources = sorted({*script.sources, *[source.url for source in usable]})
    return script


def available_providers(cfg: dict) -> list[dict]:
    return [
        {"id": name, "label": _LABELS[name], "ready": provider_ready(name)}
        for name in provider_order(cfg)
    ]


def topic_list(cfg: dict) -> list[dict]:
    used = set(used_topics())
    return [
        {
            "id": str(t["id"]),
            "title": str(t.get("title") or t["id"]),
            "used": str(t["id"]) in used,
        }
        for t in _load_topics(cfg)
    ]
