"""Palabras clave, traducción y relevancia para búsquedas visuales.

Se usa desde `script_gen` (para crear consultas cuando no hay IA o cuando la
IA no devuelve una) y desde `media` (para filtrar resultados irrelevantes o
abstractos). Mantenerlo en un solo sitio evita duplicar listas y criterios.
"""
from __future__ import annotations

import html
import re
import unicodedata

ABSTRACT_TERMS = (
    "abstract", "background", "gradient", "wallpaper", "texture",
    "pattern", "bokeh", "particle", "motion background", "loop background",
    # Gráficos estáticos que no son un fragmento real del acontecimiento.
    "shakemap", "shake map", "infographic", "infografía", "infografia",
    "diagram", "chart", "clipart", "clip art", "logo", "icon",
    "poster", "flyer", "rendering", "3d render", "illustration", "vector",
    "djvu", "pdf", "book scan", "title page",
    # Objetos de museo/documentos: no son un fragmento del acontecimiento.
    "museum", "artifact", "artefact", "exhibit", "display", "antiquarium", "fresco",
    "mosaic", "engraving", "lithograph", "sketch", "drawing", "plate",
    "design", "designs", "ornament", "ornaments", "border",
)

# Términos que NUNCA valen, aunque la consulta los pida: son arte, objetos de
# museo o escaneos, no un fragmento real del acontecimiento.
HARD_ABSTRACT = {
    "museum", "artifact", "artefact", "exhibit", "display", "antiquarium", "fresco",
    "mosaic", "engraving", "lithograph", "sketch", "drawing", "plate",
    "design", "designs", "ornament", "ornaments", "border",
    "djvu", "pdf", "book scan", "title page", "illustration", "clipart",
    "clip art", "rendering", "vector", "pattern", "statue", "sculpture",
    "relief", "reliefs", "bas-relief", "bas relief", "basrelief",
    "inscription", "inscriptions", "epigraph", "epigraphy", "stele", "stela",
    "plaque", "sarcophagus", "sarcophagi", "frieze", "friezes",
    "watercolor", "watercolour", "watercolors", "watercolours",
    "print", "prints", "scan", "scans", "etching", "etchings", "woodcut",
    "woodcuts", "album", "albumen", "albumin", "rijksmuseum", "vedute",
    "titel op object", "serietitel", "archive", "archival",
    "manuscript", "painting", "wellcome",
}

# Palabras que por sí solas no identifican la escena: aparecen en títulos de
# cualquier tema («cable car» no es una fábrica de coches por tener «car»).
WEAK_VISUAL = {
    "car", "cars", "line", "lines", "point", "view", "europe", "european",
    "world", "city", "street", "streets", "road", "roads", "stone", "stones",
    "wall", "walls", "building", "buildings", "house", "houses", "water",
    "people", "person", "man", "woman", "area", "place", "large", "small",
    "new", "old", "white", "black", "red", "green", "blue", "high", "low",
    "north", "south", "east", "west", "national", "international", "modern",
    "floor", "room", "interior", "furniture", "bed", "decor", "apartment",
    "hotel", "pillow", "comfortable", "living",
}

STOP = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del",
    "al", "a", "en", "y", "o", "que", "por", "para", "con", "sin", "es",
    "son", "se", "su", "sus", "lo", "le", "les", "no", "si", "ya", "mas",
    "más", "como", "pero", "porque", "cuando", "donde", "qué", "tu", "te",
    "me", "mi", "yo", "según", "segun", "tras", "sobre", "entre", "desde",
    "hasta", "este", "esta", "estos", "estas", "ese", "esa", "esos",
    "esas", "han", "ha", "fue", "ser", "hay", "muy", "también", "tambien",
    "solo", "sólo", "todo", "toda", "todos", "todas", "otro", "otra",
}

# Nombres de medios y verbos de atribución: no describen una imagen.
QUERY_NOISE = {
    "yahoo", "bbc", "cnn", "reuters", "ap", "efe", "rfi", "google",
    "youtube", "noticias", "news", "hoy", "informo", "informó", "dijo",
    "declaro", "declaró", "aseguro", "aseguró", "explico", "explicó",
    "según", "segun", "podria", "podría", "puede", "sera", "será",
    "ademas", "además",
}

# Términos frecuentes en noticias en español → inglés (las APIs de stock
# entienden mucho mejor el inglés).
ES_EN = {
    "guerra": "war", "terremoto": "earthquake", "terremotos": "earthquakes",
    "inundacion": "flood", "inundación": "flood", "inundaciones": "floods",
    "incendio": "fire", "incendios": "fires", "accidente": "accident",
    "accidentes": "accidents", "gobierno": "government",
    "presidente": "president", "elecciones": "elections",
    "economia": "economy", "economía": "economy", "inflacion": "inflation",
    "inflación": "inflation", "protesta": "protest", "protestas": "protests",
    "ataque": "attack", "explosion": "explosion", "explosión": "explosion",
    "tornado": "tornado", "huracan": "hurricane", "huracán": "hurricane",
    "sequia": "drought", "sequía": "drought", "pandemia": "pandemic",
    "vacuna": "vaccine", "tecnologia": "technology",
    "tecnología": "technology", "inteligencia": "artificial intelligence",
    "videojuego": "video game", "futbol": "football", "fútbol": "football",
    "musica": "music", "música": "music", "cine": "cinema",
    "ciencia": "science", "espacio": "space", "cohete": "rocket",
    "crisis": "crisis", "muertos": "victims", "heridos": "injured",
    "rescate": "rescue", "damnificados": "victims", "ejercito": "army",
    "ejército": "army", "soldados": "soldiers", "misil": "missile",
    "tanque": "tank", "refugiados": "refugees", "hospital": "hospital",
    "medicos": "doctors", "médicos": "doctors", "escuela": "school",
    "niños": "children", "ninos": "children",
    "manifestantes": "protesters", "policia": "police", "policía": "police",
    "fuego": "fire", "humo": "smoke", "ruinas": "rubble",
    "edificios": "buildings", "coches": "cars", "carretera": "road",
    "avion": "plane", "avión": "plane", "barco": "ship", "tren": "train",
    "dolares": "dollars", "dólares": "dollars", "millones": "millions",
    "porcentaje": "percent", "datos": "data", "informe": "report",
    "ley": "law", "congreso": "congress", "justicia": "justice",
    "tribunal": "court", "corrupcion": "corruption",
    "corrupción": "corruption", "empresa": "company", "mercado": "market",
    "bolsa": "stock market", "petroleo": "oil", "petróleo": "oil",
    "energia": "energy", "energía": "energy", "clima": "climate",
    "medioambiente": "environment", "contaminacion": "pollution",
    "contaminación": "pollution", "animales": "animals",
    "especie": "species", "descubrimiento": "discovery",
    "investigacion": "research", "investigación": "research",
    "estudio": "study", "universidad": "university", "robot": "robot",
    "chip": "chip", "satelite": "satellite", "satélite": "satellite",
    "mision": "mission", "misión": "mission", "lanzamiento": "launch",
    "aplicacion": "app", "aplicación": "app", "redes": "social media",
    "internet": "internet", "ciberseguridad": "cybersecurity",
    "hackeo": "hacking", "privacidad": "privacy", "salud": "health",
    "enfermedad": "disease", "virus": "virus", "agua": "water",
    "alimento": "food", "precio": "price", "precios": "prices",
    # Política, justicia y sociedad (frecuentes en noticias)
    "dama": "first lady", "donaciones": "donations", "donacion": "donation",
    "donación": "donation", "cuentas": "accounts", "ordena": "orders",
    "rendir": "testify", "fiscal": "prosecutor", "juez": "judge",
    "corte": "court", "demanda": "lawsuit", "acusacion": "accusation",
    "acusación": "accusation", "senado": "senate", "senador": "senator",
    "partido": "political party", "votos": "votes",
    "campaña": "campaign", "campana": "campaign", "impuestos": "taxes",
    "deuda": "debt", "banco": "bank", "dinero": "money", "ayuda": "aid",
    "victima": "victim", "víctima": "victim", "victimas": "victims",
    "víctimas": "victims", "familia": "family", "familias": "families",
    "comunidad": "community", "ciudad": "city", "pais": "country",
    "país": "country", "region": "region", "región": "region",
    "costa": "coast", "mar": "sea", "tierra": "land",
    "edificio": "building", "casa": "house", "casas": "houses",
    "calle": "street", "puente": "bridge", "metro": "subway",
    "trabajadores": "workers", "huelga": "strike", "marcha": "march",
    "bomba": "bomb", "atentado": "attack", "terrorismo": "terrorism",
    "paz": "peace", "acuerdo": "agreement", "cumbre": "summit",
    "discurso": "speech", "multitud": "crowd", "vecinos": "neighbors",
    "voluntarios": "volunteers", "hospital": "hospital",
    "ambulancia": "ambulance", "sirena": "siren",
    # Lugares y verbos de noticia
    "japon": "japan", "japón": "japan", "colombia": "colombia",
    "mexico": "mexico", "méxico": "mexico", "espana": "spain",
    "españa": "spain", "chile": "chile", "peru": "peru", "perú": "peru",
    "argentina": "argentina", "venezuela": "venezuela", "brasil": "brazil",
    "estados": "united", "unidos": "states", "francia": "france",
    "alemania": "germany", "italia": "italy", "china": "china",
    "rusia": "russia", "ucrania": "ukraine", "israel": "israel",
    "gaza": "gaza", "turquia": "turkey", "turquía": "turkey",
    "sacude": "strikes", "azota": "hits", "centro": "center",
    "sur": "south", "norte": "north", "este": "east", "oeste": "west",
    "tragedia": "tragedy", "damnificados": "victims",
    "esperando": "waiting", "meses": "months", "tres": "three",
    "miles": "thousands", "heridos": "injured", "muertos": "dead",
    "reportan": "reported", "deja": "leaves", "al": "at",
    "magnitud": "magnitude", "primera": "first", "siguen": "continue",
    "pompeya": "pompeii", "vesubio": "vesuvius", "herculano": "herculaneum",
    "roma": "rome", "romano": "roman", "romana": "roman",
    "napoles": "naples", "nápoles": "naples",
}


# Palabras que describen cualquier imagen antigua o genérica: no demuestran
# que el recurso sea el de esta escena concreta.
GENERIC_VISUAL = {
    "ancient", "historic", "historical", "antique", "old", "ruins", "ruin",
    "building", "architecture", "monument", "site", "landscape", "view",
    "scene", "background", "image", "photo", "travel", "tourism",
    "roman", "greek", "medieval", "cinematic", "aerial", "footage", "clip",
    "closeup", "panorama", "panoramic", "modern", "city", "urban",
    "archaeological", "archaeology", "human", "stone", "ground",
    "mount", "monte", "drone",
}

# Nombres propios: si la escena los pide, el recurso debe mencionarlos.
NAMED = {
    "pompeii", "vesuvius", "herculaneum", "rome", "naples", "milan",
    "venice", "italy", "spain", "japan", "colombia", "mexico", "chile",
    "peru", "argentina", "venezuela", "brazil", "france", "germany",
    "china", "russia", "ukraine", "israel", "gaza", "turkey", "london",
    "paris", "washington", "madrid",
}

# Términos que, por sí solos, coinciden con demasiados recursos distintos
# (un «charcoal» puede ser una inscripción o una bolsa de carbón).
AMBIGUOUS = {
    "charcoal", "coin", "money", "stone", "wall", "art", "painting",
    "house", "tree", "people", "man", "woman", "water", "fire", "black",
    "white", "light", "dark", "nature", "summer", "holiday",
    "plaster", "cast", "casts", "body", "statue",
    "earthquake", "eruption", "cloud", "sky", "brush", "dust",
    "surge", "explosion",
}


# Palabras que suelen abrir una frase con mayúscula sin ser nombres propios.
# Se usan para no confundir «Durante» o «Nuevas» con el protagonista.
COMMON_CAPS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo", "le", "les",
    "de", "del", "al", "a", "en", "y", "o", "que", "por", "para", "con",
    "sin", "es", "son", "se", "su", "sus", "no", "si", "ya", "mas", "más",
    "como", "pero", "porque", "cuando", "donde", "qué", "tu", "te", "me",
    "mi", "yo", "según", "segun", "tras", "sobre", "entre", "desde", "hasta",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "han",
    "ha", "fue", "ser", "hay", "muy", "también", "tambien", "solo", "sólo",
    "todo", "toda", "todos", "todas", "otro", "otra", "durante", "hoy",
    "ahora", "además", "ademas", "nuevas", "nuevos", "nueva", "nuevo",
    "recientes", "reciente", "muchos", "muchas", "tanto", "tanta", "tantos",
    "tantas", "incluso", "mientras", "aunque", "así", "asi", "cada", "gran",
    "grande", "grandes", "primer", "primera", "primero", "último", "ultimo",
    "última", "ultima", "dos", "tres", "cuatro", "cinco", "miles", "millones",
    "cientos", "cientas", "imaginabas", "cuéntanos", "cuentanos", "suscríbete",
    "suscribete", "déjanos", "dejanos", "comenta", "comparte", "descubre",
    "mira", "conoce", "entra", "visita", "sigue", "opinas", "crees",
    "piensas", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    "lunes", "martes", "miércoles", "miercoles", "jueves", "viernes",
    "sábado", "sabado", "domingo", "the", "an", "in", "on", "of", "and",
    "or", "but",
}

# Pistas de que un nombre propio es un lugar, no una persona. Así una escena
# de «Monte Vesubio» no exige que el recurso sea un retrato de «Vesubio».
PLACE_HINTS = {
    "monte", "mount", "volcan", "volcano", "ciudad", "city", "pais",
    "country", "estado", "estados", "reino", "union", "republica",
    "republic", "provincia", "region", "isla", "island", "mar", "sea",
    "rio", "river", "lago", "lake", "valle", "valley", "costa", "coast",
    "norte", "sur", "este", "oeste", "nueva", "nuevo", "san", "santa",
    "santo", "casa", "blanca", "white", "house", "capitol", "capitolio",
    "united", "states", "naciones", "nations", "angeles", "new", "york",
    "pompeya", "pompeii", "vesubio", "vesuvius", "herculano",
    "herculaneum", "roma", "rome", "napoles", "naples", "italia", "italy",
    "espana", "spain", "colombia", "mexico", "chile", "peru", "argentina",
    "venezuela", "brasil", "brazil", "francia", "france", "alemania",
    "germany", "china", "rusia", "russia", "ucrania", "ukraine", "israel",
    "gaza", "turquia", "turkey", "londres", "london", "paris", "washington",
    "madrid", "japon", "japan", "iran", "tehran", "teheran", "irak", "iraq",
    "siria", "syria", "libano", "lebanon", "afganistan", "afghanistan",
    "corea", "korea", "india", "pakistan", "egipto", "egypt", "grecia",
    "greece", "portugal", "belgica", "belgium", "holanda", "netherlands",
    "suiza", "switzerland", "suecia", "sweden", "noruega", "norway",
    "polonia", "poland", "austria", "hungria", "hungary", "irlanda",
    "ireland", "escocia", "scotland", "canada", "australia", "unidos",
    "britain", "bretana", "england", "inglaterra", "california", "texas",
    "florida", "chicago", "boston", "miami", "houston",
}

# Cargos genéricos: solos no identifican a nadie, así que no valen como
# «sujeto» de una búsqueda visual.
ROLE_WORDS = {
    "gobierno", "presidente", "expresidente", "congreso", "senado", "corte",
    "fiscal", "juez", "policia", "ejercito", "autoridades", "investigadores",
    "cientificos", "expertos", "arqueologos", "geologos", "vecinos",
    "habitantes", "fiscalia", "tribunal",
}


def proper_nouns(text: str, limit: int = 3) -> list[str]:
    """Nombres propios del texto: personas, lugares y organizaciones.

    Solo acepta palabras con mayúscula inicial (o siglas). Descarta las que
    abren frase por gramática («Durante», «Nuevas»…) para que el sujeto sea
    el nombre real y no una palabra de relleno.
    """
    raw = html.unescape(str(text or ""))
    cap = r"(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+|[A-ZÁÉÍÓÚÜÑ]{2,})"
    pattern = rf"\b{cap}(?:\s+(?:(?:de|del|la|las|los|y|van|von|da|di)\s+)?{cap})*"
    out: list[str] = []
    seen: set[str] = set()
    for chunk in re.findall(pattern, raw):
        words = [re.sub(r"^[^\wÁÉÍÓÚÜÑáéíóúüñ]+|[^\wÁÉÍÓÚÜÑáéíóúüñ]+$", "", w)
                 for w in chunk.split()]
        words = [w for w in words if w]
        while words and words[0].lower() in COMMON_CAPS:
            words.pop(0)
        while words and words[-1].lower() in COMMON_CAPS:
            words.pop()
        if not words or all(w.lower() in COMMON_CAPS for w in words):
            continue
        if len(words) == 1 and (len(words[0]) < 3 or words[0].lower() in STOP):
            continue
        phrase = " ".join(words[:4])
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(phrase)
        if len(out) >= limit:
            break
    return out


def main_subject(text: str) -> str:
    """Sujeto principal del texto: una persona con nombre propio si la hay.

    En «Desde la Casa Blanca, Donald Trump anunció…» el sujeto es Trump, no el
    edificio. Los lugares quedan como respaldo cuando no hay persona nombrada.
    """
    names = proper_nouns(text, limit=6)
    if not names:
        return ""
    for name in names:
        if person_subject(name):
            return name
    for name in names:
        if len(name.split()) >= 2:
            return name
    return names[0]


def subject_tokens(text: str) -> list[str]:
    """Tokens útiles de un nombre propio (incluye siglas cortas como «Xi»)."""
    return [word for word in re.findall(r"[a-z0-9]{2,}",
                                        plain_norm(translate(text)))
            if word not in STOP and word not in QUERY_NOISE]


def person_subject(text: str) -> bool:
    """¿El sujeto parece el nombre de una persona?

    Se exige capitalización de nombre propio («Donald Trump»), no una frase
    descriptiva («Cargo ship», «Stock market») que solo lleva mayúscula
    inicial. Así el anclaje estricto no rechaza recursos válidos de escenas
    que no hablan de una persona concreta.
    """
    raw_words = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", str(text or ""))
    words = [plain_norm(word) for word in raw_words]
    if not words or any(word in PLACE_HINTS for word in words):
        return False
    if len(words) == 1:
        return words[0] not in ROLE_WORDS and len(words[0]) >= 3
    # Cada palabra significativa debe ir capitalizada: «Donald Trump» sí;
    # «Cargo ship» o «European automotive industry» no.
    skip = {"de", "del", "la", "las", "los", "van", "von", "da", "di", "y"}
    significant = [word for word in raw_words
                   if len(word) > 2 and word.lower() not in skip]
    if not significant:
        return False
    capitalized = [word for word in significant if word[0].isupper()]
    return len(capitalized) == len(significant)


def plain(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).lower()


def plain_norm(text: str) -> str:
    raw = unicodedata.normalize("NFKD", plain(text))
    return "".join(ch for ch in raw if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]{3,}", plain_norm(text)) if t not in STOP]


def translate(text: str) -> str:
    words = re.findall(r"[a-záéíóúüñ0-9]+", (text or "").lower())
    out: list[str] = []
    for word in words:
        if word in STOP or word in QUERY_NOISE:
            continue
        for part in ES_EN.get(word, word).split():
            if part not in out:
                out.append(part)
    return " ".join(out)


def scene_query(text: str, title: str = "", limit: int = 5) -> str:
    """Consulta visual corta: primero lo específico de la escena, luego el tema.

    El título solo rellena si la frase no aporta palabras útiles. Así una
    escena sobre «justicia» no se convierte en una búsqueda de «terremoto».
    """
    def words_of(value: str) -> list[str]:
        return [word for word in translate(value).split()
                if word not in QUERY_NOISE]

    parts: list[str] = []
    for word in words_of(text) + words_of(title):
        if word not in parts:
            parts.append(word)
        if len(parts) >= limit:
            break
    return " ".join(parts) or (title or "news")


def relevant(text: str, query: str, topic_tokens: set[str] | None = None) -> bool:
    """¿El recurso habla de la escena?

    Si conocemos el tema del vídeo (``topic_tokens``), la primera palabra de
    la consulta suele ser el tema genérico («pompeii»). En ese caso exigimos
    una palabra *específica* de la escena, para que unas ruinas genéricas no
    sustituyan a una escena sobre esqueletos.
    """
    raw = plain_norm(text)
    if not raw.strip():
        return True
    words = tokens(query)
    if not words:
        return True
    raw_words = set(re.findall(r"[a-z0-9]{2,}", raw))

    def hit(word: str) -> bool:
        if word in raw_words:
            return True
        # Singular/plural: «cast»/«casts», «ruin»/«ruins».
        bare = word.rstrip("s")
        if bare in raw_words or f"{bare}s" in raw_words:
            return True
        # Variantes largas («pompei»/«pompeii», «plaster»/«plasters»), pero
        # nunca prefijos cortos: «casts» no debe colar por «cast» de «castle».
        for item in raw_words:
            if len(item) >= 6 and len(word) >= 6 and (
                    item.startswith(word) or word.startswith(item)):
                return True
        return False

    matches = [word for word in words if hit(word)]
    if not matches:
        return False

    # «ancient», «ruins», «city»… describen casi cualquier imagen del tema.
    strong = [word for word in matches if word not in GENERIC_VISUAL]
    if not strong:
        return False

    # Palabras propias de la escena (ni tema global ni descriptores genéricos).
    topic = topic_tokens or set()
    distinctive = [word for word in words
                   if word not in topic and word not in GENERIC_VISUAL]
    if not distinctive:
        # La escena solo pide el tema: cualquier recurso del tema sirve.
        return True

    # Si la escena abre con un nombre propio («vesuvius…», «herculaneum…»),
    # ese nombre manda: una erupción del St. Helens no es el Vesubio.
    named = [word for word in distinctive if word in NAMED]
    if named and distinctive[0] in NAMED:
        if not all(word in strong for word in named):
            return False
        # Si la escena añade varios detalles («vesuvius volcano crater
        # smoke»), no basta con que el lugar aparezca al fondo del plano.
        others = [word for word in distinctive if word not in NAMED]
        if len(others) >= 2 and not any(word in strong for word in others):
            return False
        return True

    # Un lugar mencionado de pasada («justice colombia…») no basta: la
    # escena exige su propio sujeto. Si no, basta una palabra no ambigua.
    subject = [word for word in distinctive if word not in NAMED] or distinctive
    matched = [word for word in subject if word in strong]
    if not matched:
        return False
    if any(word not in AMBIGUOUS for word in matched):
        return True
    # Solo quedan coincidencias ambiguas («wall», «cloud»…): hacen falta al
    # menos dos pistas. Si la escena entera es una sola palabra ambigua, el
    # recurso debe al menos pertenecer al tema del vídeo.
    if len(matched) >= 2:
        return True
    if len(subject) <= 1:
        return not topic or any(word in raw_words for word in topic)
    return False


def _has_term(raw: str, term: str) -> bool:
    """Coincidencia por palabra completa, aceptando plural («fresco(s)»)."""
    pattern = r"(?<![a-z])" + re.escape(term) + r"(s|es)?(?![a-z])"
    return re.search(pattern, raw) is not None


def not_abstract(text: str, query: str) -> bool:
    raw = plain_norm(text)
    wanted = plain_norm(query)
    # Escaneos, grabados y fotos de publicaciones antiguas: «(1905)»,
    # «pub 1880»… no son la escena (salvo que la escena pida ese año).
    if re.search(r"\b1[6-9]\d{2}\b", raw) and not re.search(r"\b1[6-9]\d{2}\b", wanted):
        return False
    # Fotos de archivo del siglo XIX: documentan excavaciones o lugares hace
    # más de un siglo, no el tema actual de la escena.
    for phrase in ("19th century", "end of the 19th", "late 19th"):
        if phrase in raw and phrase not in wanted:
            return False
    # Arte, museo y escaneos: nunca valen, aunque la consulta los pida.
    if any(_has_term(raw, word) for word in HARD_ABSTRACT):
        return False
    for word in ABSTRACT_TERMS:
        if word in HARD_ABSTRACT:
            continue
        if _has_term(raw, word) and not _has_term(wanted, word):
            return False
    return True
