# Sistema modular de gráficos

Arquitectura para overlays y gráficos en Shorts (9:16, 1080x1920). **No reemplaza FFmpeg**: los motores generan assets (PNG/video/HTML) y FFmpeg los compone en `video.py`. **HyperFrames es opcional**.

## Motores

| Motor | Estado | Requisitos | Qué genera |
|---|---|---|---|
| `svg` | Integrado | Navegador headless (Chrome/Edge) recomendado; fallback a base FFmpeg | Títulos, lower-thirds, ticker, paneles de stats (SVG/CSS → PNG → overlay) |
| `hyperframes` | Integrado (opcional) | Node + HyperFrames + navegador (`tools/hyperframes`, plantillas) | Fondos animados HTML → MP4 |
| `lottie` | Integrado (opcional) | `assets/graphics/lottie/*.json` + navegador con acceso a lottie-web | Overlays desde animaciones Lottie (frame/overlay) |
| `remotion` | Integrado (opcional) | Proyecto Remotion en `assets/graphics/remotion` (package.json) + Node/npx | Overlays/gráficos renderizados con Remotion (composición `Graphics`) |

## Estilos automáticos (`graphics.style=auto`)

- `documental` – profesional, sobrio
- `periodistico` – noticias: lower-third, ticker, stats
- `cinematografico` – título/estética cinematográfica
- `tecnologico` – tech/IA, acentos modernos
- `videojuegos` – gaming, alto contraste
- `stats` – énfasis en datos/estadísticas

El estilo se detecta por contenido del guion/título/tema o se puede forzar en `config.yaml`.

## Configuración (`config.yaml`)

```yaml
graphics:
  provider: auto   # none|auto|svg|hyperframes|lottie|remotion
  style: auto      # auto|documental|periodistico|cinematografico|tecnologico|videojuegos|stats
```

## Agregar un motor nuevo

1. Crear `src/shorts/graphics_engines/mi_motor.py` con una clase:
   - `name = "mi_motor"`
   - `available(self, cfg) -> bool`
   - `prepare(self, cfg, script, duration, work, style) -> GraphicsPlan`
2. Registrar en `graphics_engines/__init__.py` dentro de `_ensure_registered()`.
3. Devolver `OverlayAsset` para overlays (PNG/video) y/o `backgrounds` (MP4). FFmpeg los integrará.

## Contrato de `OverlayAsset`

- `path`, `kind` (`image`/`video`), `start`, `duration`, `x/y/w/h`, `alpha`, `fade_in/fade_out`, `label`
- Los overlays de imagen se copian al directorio de trabajo y se componen con FFmpeg (`overlay` + timing).

## Notas

- Las funciones actuales de FFmpeg y el montaje existente se mantienen intactos.
- Si un motor no está disponible o falla, el sistema continúa con el motor siguiente o con FFmpeg base.