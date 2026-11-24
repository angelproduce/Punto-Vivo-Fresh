# Publicar PUNTO VIVO FRESH en Internet (Render + Supabase)

> **Nota:** la opción elegida finalmente es **Vercel**. Consulta [`DEPLOY_VERCEL.md`](DEPLOY_VERCEL.md). Esta guía se conserva como alternativa y Vercel **no usa** `render.yaml`.

Esta guía publica **solo PUNTO VIVO FRESH**: menú digital, QR, registro de visitas y panel administrativo.

> El generador **ANGEL PRODUCE IA**, FFmpeg, los modelos de voz y las conexiones a YouTube/TikTok/Facebook **se quedan en tu PC exactamente como están**. No se publican ni se modifican.

El resultado es un enlace HTTPS público, por ejemplo `https://punto-vivo-fresh.onrender.com`, que puedes imprimir en el QR. Tu ordenador **no necesita estar encendido**.

---

## 0. Antes de empezar

Necesitas tres cuentas gratuitas:

1. **Supabase** — base de datos PostgreSQL y fotos. <https://supabase.com>
2. **Render** — servidor Python con HTTPS. <https://render.com>
3. **GitHub** — Render despliega desde un repositorio Git; no admite subir una carpeta suelta. <https://github.com>

No hace falta comprar un dominio: Render regala un subdominio `*.onrender.com`.

---

## 1. Subir el código a GitHub

El proyecto **todavía no tiene repositorio Git**. Desde la carpeta del proyecto:

```bat
git init
git add .
git commit -m "PUNTO VIVO FRESH listo para Render"
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

Antes de `git add`, comprueba que `.gitignore` excluye `.env`, `state/`, `.venv/`, `tools/` y `.cache/`. Puedes verificarlo con:

```bat
git status
```

Si aparece `.env`, `state/` o cualquier clave, **no subas nada** y corrige `.gitignore` primero. Las credenciales del generador y la contraseña inicial de PUNTO VIVO no deben llegar nunca a GitHub.

> El repositorio puede ser privado: Render puede conectarse a repos privados desde su dashboard.

---

## 2. Preparar Supabase

1. En Supabase, **New project**. Elige la región más cercana y guarda la contraseña de la base de datos (no la necesita la app).
2. Abre **SQL Editor → New query**.
3. Pega **todo** el contenido de [`database/supabase_schema.sql`](database/supabase_schema.sql) y pulsa **Run**. Si ya lo ejecutaste antes, vuelve a ejecutarlo por completo.
4. Verifica que no haya errores. El script crea:
   - tablas con IDs enteros (compatibles con las rutas actuales `/api/producto/{id}`),
   - RLS (el público solo ve categorías activas y productos disponibles),
   - las RPC `registrar_visita`, `pv_allow_event` y `crear_pedido`,
   - tablas de pedidos, artículos, pagos y direcciones,
   - el bucket público `menu-images` para las fotos.
5. En **Project Settings → API** copia dos valores:
   - **Project URL** → `PV_SUPABASE_URL`
   - **service_role** (secret) → `PV_SUPABASE_SERVICE_ROLE_KEY`

> Usa **service_role**, no la clave `anon`: la app escribe visitas, sesiones y administradores desde el servidor. La service_rolekey **nunca** se envía al navegador ni se guarda en el QR.

6. Genera el secreto de cookies en una terminal local:

```bat
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Copia el resultado como `PV_SECRET_KEY`.

---

## 3. Crear el servicio en Render

### Opción A: Blueprint (recomendada)

1. Render → **New → Blueprint**.
2. Conecta el repositorio de GitHub. Render detecta `render.yaml` en la raíz.
3. Rellena los secretos que te pida: `PV_SUPABASE_URL`, `PV_SUPABASE_SERVICE_ROLE_KEY`, `PV_SECRET_KEY`.
4. En `PV_PUBLIC_BASE_URL` puedes dejar el valor vacío ahora (se rellena en el paso 5).
5. **Apply**. Render instala `requirements-punto.txt` y arranca:

```text
uvicorn punto_vivo.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'
```

### Opción B: Web Service manual

Si prefieres no usar Blueprint: **New → Web Service**, conecta el mismo repositorio y configura:

| Campo | Valor |
|---|---|
| Runtime | Python |
| Plan | Free |
| Build Command | `pip install -r requirements-punto.txt` |
| Start Command | `uvicorn punto_vivo.app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'` |
| Health Check Path | `/healthz` |

Variables de entorno: las mismas del Blueprint. `PV_TRUST_PROXY=1`, `PV_SUPABASE_BUCKET=menu-images`, `PYTHONPATH=/opt/render/project/src` y los secretos del paso 2.

> **No fijes el puerto 8790 en Render.** Render asigna `PORT` automáticamente; la app ya lo respeta. El 8790 sigue siendo solo para tu PC.

---

## 4. Crear el administrador en producción

El administrador que ya tienes en tu PC vive en SQLite local y **no existe** en Supabase. Crea uno nuevo en producción, eligiendo una de estas dos formas:

**A) Automática:** rellena `PV_ADMIN_EMAIL` y `PV_ADMIN_PASSWORD` en Render antes del primer arranque. La app crea el admin al iniciar. **Borra o cambia la contraseña después** si la pusiste temporal.

**B) Desde Render Shell:** abre el servicio → pestaña **Shell** y ejecuta:

```bash
python -m punto_vivo.admin create --email tu@correo.com
```

Te pedirá la contraseña (mínimo 10 caracteres) de forma segura. Con `python -m punto_vivo.admin list` verifies los admins creados.

---

## 5. Fijar la URL pública y descargar el QR

1. Cuando el despliegue termine, Render muestra la URL: `https://<tu-servicio>.onrender.com`.
2. Cópiala en `PV_PUBLIC_BASE_URL` (sin barra final y sin `/bienvenida`) y guarda. Render redespliega automáticamente.
3. Entra en `https://<tu-servicio>.onrender.com/admin/login`.
4. Descarga el QR desde el apartado de código QR (PNG o SVG). Codificará `https://<tu-servicio>.onrender.com/bienvenida`.
5. Imprime ese QR. Si cambias de dominio más adelante, actualiza la variable y **vuelve a descargar el QR**.

---

## 6. Comprobación final

| Comprobación | Resultado esperado |
|---|---|
| `GET /healthz` | `{"status":"ok","app":"PUNTO VIVO FRESH"}` |
| `GET /bienvenida` | Formulario de registro |
| `GET /admin/login` | Pantalla de acceso |
| Registrar una visita de prueba | Fila nueva en Supabase → Table Editor → `visits` |
| Subir una foto desde el panel | Archivo en Supabase → Storage → `menu-images` |
| Escanear el QR impreso | Abre el registro con el dominio HTTPS |

Si el primer acceso tarda ~1 minuto, es normal: el plan gratuito de Render duerme el servicio tras ~15 min de inactividad y lo despierta en la primera petición.

---

## 7. Límites y avisos del plan gratuito

- **Render Free:** 0.1 CPU / 512 MB, reposo tras ~15 min sin tráfico, arranque en ~1 min, sistema de archivos efímero, 750 h/mes. Pensado para pruebas y uso personal, no para producción crítica.
- **Supabase Free:** 500 MB de base de datos, 1 GB de Storage, subidas de 50 MB, 5 GB de transferencia.
- **Supabase pausa proyectos con poca actividad durante 7 días.** Si la app deja de responder, entra al dashboard de Supabase y pulsa **Resume project**; los datos se conservan hasta 1 año. Visitar la app o el dashboard genera actividad.
- Por el disco efímero de Render, **toda la persistencia pasa por Supabase**. Nunca guardes datos importantes en `state/` ni en `static/uploads/` dentro de Render.

Esto es exactamente por lo que se añadió `repository.py`: en local sigue usando SQLite; en Render usa Supabase automáticamente al detectar `PV_SUPABASE_URL` y `PV_SUPABASE_SERVICE_ROLE_KEY`.

---

## 8. Solución de problemas

| Síntoma | Causa probable / solución |
|---|---|
| Error `Supabase ... 401/403` | Falta la service_role key o es la anon. Revisa las variables en Render y que el SQL se ejecutó completo. |
| `No se pudo guardar la visita` o el pedido | RPCs no creadas: vuelve a ejecutar `supabase_schema.sql` en SQL Editor (incluye `registrar_visita` y `crear_pedido`). |
| El QR apunta a `127.0.0.1` | `PV_PUBLIC_BASE_URL` está vacío. Rellénalo y descarga el QR de nuevo. |
| `Origen no permitido` (403) al usar el panel | `PV_TRUST_PROXY` debe ser `1` para que se lea `X-Forwarded-Host`. |
| Las fotos salen rotas tras reiniciar | El bucket `menu-images` debe ser público y `PV_SUPABASE_BUCKET` debe coincidir. |
| No puedo entrar al panel | El admin local no existe en Supabase; crea uno con Render Shell (paso 4). |
| La primera petición tarda mucho | Arranque en frío del plan gratuito; espera ~1 min. |

---

## 9. Lo que NO cambia

- `python -m punto_vivo` en tu PC sigue usando **SQLite + `static/uploads/`** si no defines las variables `PV_SUPABASE_*`.
- El puerto local sigue siendo **8790**; el generador sigue en **8765**.
- Ni FFmpeg, ni los modelos, ni los tokens OAuth de YouTube/TikTok/Facebook se tocan: esa parte sigue funcionando solo en local.

---

## 10. Despliegue en Vercel o Cloudflare

**No se recomienda para esta app.** Vercel limita funciones Python a 5 minutos y Cloudflare Workers Free a 10 ms de CPU, además de no ofrecer disco persistente. Render ejecuta el FastAPI completo y es la opción que encaja con el proyecto tal como está. No se han creado configuraciones para esas plataformas porque no funcionarían correctamente sin rediseñar la aplicación.
