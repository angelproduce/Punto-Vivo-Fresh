# ANGEL PRODUCE IA

> **Nuevo módulo incluido: PUNTO VIVO FRESH.** Menú digital, registro de clientes y control de visitas por QR. Ábrelo con **`PUNTO VIVO FRESH.exe`** (o `INICIAR PUNTO VIVO FRESH.cmd`) en el puerto **8790**, sin interferir con el generador de vídeos (8765). Para regenerar el EXE: `build_punto_exe.cmd`. Documentación completa en [`punto_vivo/README.md`](punto_vivo/README.md).
>
> **Para publicarlo en Internet** sin mantener el PC encendido: [`punto_vivo/DEPLOY_VERCEL.md`](punto_vivo/DEPLOY_VERCEL.md) (Vercel + Supabase, opción elegida). Alternativa con Render: [`punto_vivo/DEPLOY_RENDER.md`](punto_vivo/DEPLOY_RENDER.md). El generador de vídeos, FFmpeg y las redes sociales siguen funcionando solo en local.

Generador automático de **shorts verticales (1080×1920)** con **tu propia voz clonada**, guion en español, subtítulos tipo karaoke y fondos animados. Todo se ejecuta en tu PC: sin cuotas, sin APIs de pago y sin instalar nada en el sistema.

Voz clonada con **Qwen3-TTS** · subtítulos con **Whisper** · gráficas animadas con **HyperFrames** · montaje con **FFmpeg**.

---

## 1. Arrancar (solo la primera vez)

1. Doble clic en **`setup.cmd`**.
   Descarga un Python portable y las librerías dentro de la propia carpeta (`tools\` y `.venv\`), además de Node, FFmpeg y el motor de gráficas HyperFrames. Tarda unos minutos y necesita internet. No toca nada del sistema.
2. Cuando termine, abre **`ANGEL PRODUCE IA.exe`** (o el acceso directo **ANGEL PRODUCE IA** del escritorio).
   Se abre la interfaz en el navegador. Para cerrarla, botón **Salir** en Ajustes.

Si algo falla con el acceso directo, usa **`ui.cmd`** (misma interfaz, sin `.exe`).

> La primera vez que generes un short se descargan los modelos de IA (~2 GB) y el proceso puede tardar **6–8 minutos**. A partir de ahí, cada short tarda unos **5–6 minutos** en un portátil normal.

---

## 2. La interfaz

**Crear** — elige cuántos shorts (1–3) y el tema (o deja «Automático» para que rote). Pulsa **Generar short** y verás el progreso paso a paso: guion → voz → fondos → subtítulos → montaje → cola. Puedes seguir usando la app mientras trabaja.

**Revisar** — cada short cae en *Pendientes*. Pulsa la tarjeta para verlo con sonido, leer el guion y decidir:
- **Aprobar** → pasa a *Aprobados*, listo para publicar.
- **Descartar** → se borra.
- **Publicar** → sube los aprobados a las plataformas conectadas. **Simular** hace todo el proceso sin subir nada.

**Mi voz** — arrastra un audio de **10–20 segundos** hablando normal y se usa como referencia para clonar tu timbre. Se puede cambiar cuando quieras: el siguiente short ya usa la voz nueva.

**Ajustes** — estado del sistema (voz, temas, disco, FFmpeg, gráficas), plataformas conectadas, acceso directo a las carpetas y botón para cerrar el servidor. En **Gráficas de los vídeos** ves las plantillas instaladas y puedes descargar más con un clic.

---

## 3. Dónde queda todo

```
output/                 vídeos sueltos (si generas desde la línea de comandos)
queue/pending/          shorts esperando tu revisión
queue/approved/         aprobados, listos para publicar
queue/published/        ya publicados
assets/voice/           tu voz de referencia (reference.wav)
assets/backgrounds/     fondos propios (opcional)
assets/music/           música de fondo (opcional)
assets/frames/          marcos decorativos 9:16 (PNG con el centro transparente)
assets/fonts/           fuentes para los textos
assets/graphics/hf/     gráficas HyperFrames (compositions/*.html) y sus plantillas
tools/                  Python, Node, FFmpeg y HyperFrames portables
logs/                   ui.log y pipeline.log (errores)
.cache/                 modelos de IA y archivos temporales
```

Cada short de la cola es una carpeta con `video.mp4`, `cover.jpg` y `meta.json` (título, hashtags, guion, duración).

---

## 4. Personalizar

**Temas y guiones** → `topics.yaml`
Cada bloque es un short: `title`, `hook`, `body`, `cta`, `hashtags`, `description`. Añade los tuyos copiando un bloque. **No hay límite de vídeos**: los temas rotan y, cuando se acaban, vuelven a empezar solos. Todo es gratis y local, sin cuotas.

**Voz y vídeo** → `config.yaml`
Duración objetivo, resolución, fps, tamaño/posición de subtítulos, velocidad de la voz, pausas, música de fondo, marca de agua, número de fondos.

**Gráficas** → `config.yaml`, sección `graphics`. Por defecto los fondos los dibuja **HyperFrames**: animaciones HTML/WebGPU renderizadas en vertical (1080×1920). La primera vez que se usa una plantilla tarda ~1,5 minutos (con Chrome completo); después queda guardada en `.cache\graphics\` y se reutiliza. Se rota entre todas las instaladas.

- `graphics.quality`: `draft` (rápido), `looks` (equilibrado) o `delivery` (máxima calidad).
- `graphics.provider: degradados` vuelve a los fondos animados de FFmpeg (plan B, siempre funciona).
- `media.source: local` usa primero tus clips de `assets/backgrounds/` y solo recurre a HyperFrames si no hay.

**Más plantillas**: botón **Instalar más plantillas** en Ajustes, o `run.cmd plantillas add swirl`. Las plantillas disponibles salen de `run.cmd plantillas`. Se guardan como HTML en `assets\graphics\hf\compositions\` y puedes retocarlas a mano.

**Marcos (plantillas)** → Ajustes → pestaña **🖼️ Marcos**. Elige **GTA 6**, **CASA ALOFOKE 3** o **Ninguno**: el marco se dibuja encima del vídeo completo y deja libre el centro, así que no tapa la imagen ni los subtítulos. La elección se guarda al instante en `video.frame_template` y se aplica al siguiente vídeo. Los PNG viven en `assets/frames/` (9:16, centro transparente); para añadir otro, copia el PNG ahí y añádelo al diccionario `FRAMES` de `src/shorts/frames.py`.

Si quieres vídeo real en lugar de gráficas:
- Mete tus clips verticales en `assets/backgrounds/`, o
- Rellena `PEXELS_API_KEY` / `PIXABAY_API_KEY` en `.env` (gratis al registrarte) y pon `media.source: auto`.

**Guiones con IA** (opcional): pon `script.provider: gemini` y `GEMINI_API_KEY` en `.env`. Sin clave se usan las plantillas de `topics.yaml`, que ya están escritas.

---

## 5. Publicar (opcional)

La app publica si le das credenciales. Sin ellas, todo lo demás funciona igual y puedes subir los vídeos a mano.

- **YouTube**: crea credenciales OAuth en Google Cloud y guarda `state/youtube_token.json` con `access_token` o `refresh_token` + `client_id` + `client_secret`.
- **TikTok**: guarda `state/tiktok_token.json` con `access_token` del Content Posting API.
- **Facebook**: pon `FACEBOOK_PAGE_TOKEN` en `.env` (y opcionalmente `publish.facebook.page_id` en `config.yaml`).

Formatos y límites los pone cada plataforma; el vídeo sale en 9:16, H.264 + AAC, listo para Shorts/Reels/TikTok.

---

## 6. Línea de comandos

```
run.cmd doctor              comprueba que todo está en su sitio
run.cmd run -n 1            genera un short
run.cmd run -n 3            genera tres seguidos
run.cmd run -t mapa-gta-6   genera un tema concreto
run.cmd run -d 120          genera un short de 2 minutos
run.cmd topics              lista los temas y cuáles están usados
run.cmd canales             lista los canales conectados
run.cmd autopilot           estado del piloto automático
run.cmd youtube title "..."  sugiere título para YouTube
run.cmd plantillas          lista las gráficas instaladas
run.cmd plantillas add swirl instala una gráfica nueva del catálogo
run.cmd list                lista la cola (pending por defecto)
run.cmd approve all         aprueba todos los pendientes
run.cmd reject <id>         descarta uno
run.cmd publish --dry-run   simula la publicación
run.cmd voice               estado de tu voz de referencia
run.cmd clean               borra temporales y fondos descargados
```

---

## 7. Problemas típicos

| Síntoma | Solución |
|---|---|
| «Falta el entorno de Python» | Ejecuta `setup.cmd` y espera a que diga *Listo*. |
| La interfaz no abre | Mira `logs\ui.log`. Si el puerto 8765 está ocupado, la app usa el siguiente libre. |
| PUNTO VIVO FRESH no abre | Mira `logs\punto_vivo.log`. Usa el puerto fijo 8790; si cambiaste el launcher, regenera con `build_punto_exe.cmd`. |
| Un short falla | Mira `logs\pipeline.log`. Los archivos a medias quedan en `.cache\work\`. |
| La voz no suena como yo | Sube otra grabación en **Mi voz**: 15 s, sin ruido, mismo micro. La clonación usa solo el timbre. |
| Va muy lento | Es normal en CPU: ~20× tiempo real. Cierra programas pesados; el resto de la app sigue usable. |
| Las gráficas salen negras o fallan | Necesitan **Chrome o Edge** instalado. Prueba `run.cmd doctor`; mientras, pon `graphics.provider: "degradados"` y la app usa sus fondos propios. |
| Quiero más gráficas | Botón **Instalar más plantillas** en Ajustes (o `run.cmd plantillas add <nombre>`). Se descargan una sola vez y quedan en la carpeta. |
| Quiero liberar espacio | `run.cmd clean` borra temporales. Los modelos (~2 GB) viven en `.cache\huggingface`. |
| He cambiado el código | Vuelve a ejecutar `build_exe.cmd` para regenerar `ANGEL PRODUCE IA.exe`. |

---

## 8. Requisitos

- Windows 10/11 de 64 bits.
- ~12 GB libres (modelos + gráficas + vídeos).
- 8 GB de RAM (16 GB recomendado).
- **Chrome o Edge** instalado (el que ya uses) para renderizar las gráficas.
- Internet solo para `setup.cmd`, la primera descarga de modelos, las plantillas nuevas y la publicación.

Todo lo demás es local: tus audios y tus vídeos no salen del ordenador.
