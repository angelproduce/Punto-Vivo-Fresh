"""Style definitions for modular graphics."""
from __future__ import annotations

STYLES = {
    "documental": {
        "label": "Documental profesional",
        "palette": {"primary": "#F5F0E8", "accent": "#C9A227", "bg": "#1A1A1A", "text": "#FFFFFF"},
        "font": "Georgia, serif",
        "lower_third": True, "title_card": True, "ticker": False, "stats": False,
    },
    "periodistico": {
        "label": "Periodístico / Noticias",
        "palette": {"primary": "#FFFFFF", "accent": "#CC0000", "bg": "#0A0A0A", "text": "#FFFFFF"},
        "font": "Arial, Helvetica, sans-serif",
        "lower_third": True, "title_card": True, "ticker": True, "stats": True,
    },
    "cinematografico": {
        "label": "Cinematográfico",
        "palette": {"primary": "#E8D5B7", "accent": "#D4AF37", "bg": "#000000", "text": "#F5F5F5"},
        "font": "Georgia, serif",
        "lower_third": False, "title_card": True, "ticker": False, "stats": False,
    },
    "tecnologico": {
        "label": "Tecnológico",
        "palette": {"primary": "#00D4FF", "accent": "#7C3AED", "bg": "#0B1020", "text": "#E6F7FF"},
        "font": "Arial, Helvetica, sans-serif",
        "lower_third": True, "title_card": True, "ticker": False, "stats": True,
    },
    "videojuegos": {
        "label": "Videojuegos",
        "palette": {"primary": "#00FF88", "accent": "#FF3366", "bg": "#111111", "text": "#FFFFFF"},
        "font": "Arial Black, Arial, sans-serif",
        "lower_third": True, "title_card": True, "ticker": False, "stats": True,
    },
    "stats": {
        "label": "Estadísticas y datos",
        "palette": {"primary": "#38BDF8", "accent": "#F59E0B", "bg": "#0F172A", "text": "#F8FAFC"},
        "font": "Arial, Helvetica, sans-serif",
        "lower_third": True, "title_card": True, "ticker": False, "stats": True,
    },
}


def get_style(name: str) -> dict:
    return STYLES.get(name, STYLES["documental"])
