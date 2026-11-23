# Publicar PUNTO VIVO FRESH en Vercel (con Supabase)

Guía para dejar **PUNTO VIVO FRESH** en un enlace HTTPS público con el plan gratuito de Vercel, sin mantener el PC encendido.

> Vercel sí sirve para esta app: son peticiones cortas y la persistencia ya está en Supabase. El generador **ANGEL PRODUCE IA** (FFmpeg, modelos, OAuth de YouTube/TikTok/Facebook) **no se publica** y sigue funcionando solo en tu PC.

La guía de Render (`DEPLOY_RENDER.md`) se conserva como alternativa, pero **Vercel no usa** `render.yaml`.

---

## 1. Límites que hay que respetar (plan Hobby gratuito)

| Límite | Valor | Cómo lo cumplimos |
|---|---|---|
| Duración de función | 300 s (configurado 60 s) | Las rutas del menú responden en milisegundos. |
| Cuerpo de petición/respuesta | **4.5 MB** | Fotos limitadas a **4 MB** en Vercel (`PV_MAX_IMAGE_BYTES=4194304`). |
| Bundle Python | 500 MB | `vercel.json` excluye generador, modelos y cachés. |
| Memoria | 2 GB | No se cargan modelos de IA. |
| Disco | Solo lectura (500 MB en `/tmp`) | Base, sesiones y fotos viven en Supabase. |

No se intenta subir vídeo ni ejecutar FFmpeg: eso no cabe en Vercel y no es parte de PUNTO VIVO.

---

## 2. Preparar Supabase

1. Crea un proyecto gratuito en <https://supabase.com> (elige la región más cercana).
2. **SQL Editor → New query**: pega todo [`database/supabase_schema.sql`](database/supabase_schema.sql) y pulsa **Run**. Si ya lo ejecutaste antes, vuelve a ejecutarlo: añade la tabla `pv_meta`, que evita que dos funciones frías siembren el menú demo a la vez.
3. Crea además el bucket, si el script no lo hiciera: Storage → New bucket → `menu-images`, marcar **Public bucket**.
4. **Project Settings → API**: copia **Project URL** (`PV_SUPABASE_URL`) y el secreto **service_role** (`PV_SUPABASE_SERVICE_ROLE_KEY`).
   - **Nunca uses la clave `anon`**: la app escribe visitas, sesiones y admins.
   - **Nunca** pongas la service_role en JavaScript, en el repo ni en el QR.
5. Genera el secreto de cookies:

```bat
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Subir el código a GitHub

Vercel despliega desde Git. El proyecto aún no tiene repositorio:

```bat
git init
git add .
git commit -m "PUNTO VIVO FRESH en Vercel"
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

`.gitignore` y `.vercelignore` ya excluyen `.env`, `state/`, `.venv/`, `tools/` y `.cache/`. Comprueba con `git status` que no aparece ningún secreto antes de subir.

---

## 4. Variables de entorno en Vercel

En el proyecto de Vercel: **Settings → Environment Variables**. Añade estas variables para **Production** (y Preview si quieres probar antes):

| Variable | Valor |
|---|---|
| `PV_SUPABASE_URL` | Project URL de Supabase |
| `PV_SUPABASE_SERVICE_ROLE_KEY` | Secreto service_role |
| `PV_SECRET_KEY` | El hex generado en el paso 2 (**obligatorio**: en Vercel no se puede escribir en disco) |
| `PV_TRUST_PROXY` | `1` (para cookies HTTPS y validación de origen) |
| `PV_SUPABASE_BUCKET` | `menu-images` |
| `PV_MAX_IMAGE_BYTES` | `4194304` (4 MB; por debajo del límite de 4.5 MB de Vercel) |
| `PV_ADMIN_EMAIL` | Tu correo de admin (opcional, ver paso 6) |
| `PV_ADMIN_PASSWORD` | Contraseña ≥ 10 caracteres (opcional) |
| `PV_PUBLIC_BASE_URL` | Déjala vacía ahora; se rellena en el paso 7 |

> Las variables solo del servidor. Vercel no las expone al navegador. Cambiar una variable exige **redeploy** para que surta efecto.

---

## 5. Desplegar

1. Vercel → **Add New → Project** → importa el repositorio de GitHub.
2. Deja **Root Directory** en la raíz del repo (no lo cambies).
3. Vercel detecta el framework **FastAPI** solo (`pyproject.toml` declara las dependencias mínimas y `tool.vercel.entrypoint = "punto_vivo.app:app"`). No hay que escribir Build Command ni Start Command.
   - El `requirements.txt` de la raíz pertenece al generador y **no se instala** en Vercel.
4. **Deploy**. En los logs debe aparecer Python 3.12 y la detección de FastAPI.
5. Comprueba `https://<tu-proyecto>.vercel.app/healthz` → `{"status":"ok","app":"PUNTO VIVO FRESH"}`.

Si usas la CLI: `npm i -g vercel` y `vercel --prod` desde la raíz del proyecto.

---

## 6. Crear el administrador en producción

El admin de tu PC está en SQLite local y **no existe** en Supabase. Elige una de estas dos formas:

**A) Por variables de entorno (la más fácil en Vercel):** rellena `PV_ADMIN_EMAIL` y `PV_ADMIN_PASSWORD` antes del primer despliegue. La app crea el admin al arrancar. Cámbiala después si la pusiste provisional. Esas variables solo crean el primer usuario; para añadir usuarios o cambiar correo/contraseña entra al panel, sección **Usuarios**.

**B) Desde tu PC apuntando a Supabase:** sin tocar archivos, en PowerShell:

```powershell
$env:PV_SUPABASE_URL='https://xxx.supabase.co'
$env:PV_SUPABASE_SERVICE_ROLE_KEY='***'
.venv\Scripts\python.exe -m punto_vivo.admin create --email tu@correo.com
```

Te pedirá la contraseña de forma segura. No pegues la service_role en el chat ni en un archivo.

---

## 7. URL definitiva y QR

1. Copia la URL que te da Vercel, p. ej. `https://punto-vivo-fresh.vercel.app`.
2. Ponla en `PV_PUBLIC_BASE_URL` (sin barra final y sin `/bienvenida`) y haz **Redeploy**.
3. Entra en `/admin/login` y descarga el QR (PNG o SVG): codificará `https://<tu-proyecto>.vercel.app/bienvenida`.
4. Vuelve a descargar el QR si cambias de dominio.

---

## 8. Verificación final

| Prueba | Esperado |
|---|---|
| `GET /healthz` | `{"status":"ok","app":"PUNTO VIVO FRESH"}` |
| `GET /bienvenida` | Formulario de registro |
| `GET /admin/login` | Pantalla de acceso |
| Registrar visita de prueba | Fila nueva en Supabase → Table Editor → `visits` |
| Subir foto desde el panel (≤ 4 MB) | Archivo en Storage → `menu-images` |
| Escanear el QR | Abre el registro en el dominio `vercel.app` |

---

## 9. Problemas típicos en Vercel

| Síntoma | Solución |
|---|---|
| `MODULE_NOT_FOUND` / `No module named fastapi` | Revisa que `pyproject.toml` conserve las 8 dependencias; Vercel prioriza ese archivo sobre `requirements.txt`. |
| Todas las rutas dan 404 | Confirma Root Directory = raíz y `tool.vercel.entrypoint = "punto_vivo.app:app"`. |
| `413 FUNCTION_PAYLOAD_TOO_LARGE` | La foto supera 4.5 MB. Reduce `PV_MAX_IMAGE_BYTES` a `4194304` y comprime la imagen antes de subirla. |
| `Origen no permitido` (403) en el panel | `PV_TRUST_PROXY=1` y redeploy. |
| El QR apunta a `127.0.0.1` | Rellena `PV_PUBLIC_BASE_URL` y descarga el QR otra vez. |
| `PV_SECRET_KEY es obligatorio...` | Añade la variable en Vercel; el disco es de solo lectura. |
| Fotos rotas | Bucket `menu-images` público y `PV_SUPABASE_BUCKET` correcto. |
| No puedo entrar al panel | Crea el admin en Supabase (paso 6); el local no se migra solo. |
| Primera petición lenta | Arranque en frío de Vercel Functions; normal en el plan gratuito. |

---

## 10. Qué queda igual

- En tu PC, `python -m punto_vivo` sigue usando **SQLite + `static/uploads/`** en el puerto **8790** si no defines `PV_SUPABASE_*`.
- El generador sigue en el puerto **8765** con FFmpeg, modelos y redes sociales intactos.
- Ninguna ruta, validación ni regla de negocio de PUNTO VIVO cambió: solo se cambió el destino del despliegue y el límite de foto en producción.
