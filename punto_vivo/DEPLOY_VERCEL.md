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
2. **SQL Editor → New query**: pega todo [`database/supabase_schema.sql`](database/supabase_schema.sql) y pulsa **Run and enable RLS** (usa siempre esa opción; **nunca** «Run without RLS»). Si ya lo ejecutaste antes, **vuelve a ejecutarlo completo**: añade `pv_meta` (evita que dos funciones frías siembren el menú demo a la vez), las tablas `delivery_addresses`, `orders`, `order_items`, `payments`, `payment_accounts`, `payment_proofs`, `admin_push_subscriptions`, el bucket privado `comprobantes` y la RPC `crear_pedido` de **8 parámetros** (la antigua de 7 se elimina). Sin este paso los pedidos y las transferencias fallarán aunque el código ya esté publicado.
3. Comprueba en Storage: `menu-images` debe ser **público** (fotos) y `comprobantes` debe ser **privado**. Si el script no los creó, crea `menu-images` con **Public bucket** y `comprobantes` sin marcar esa opción.
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
| `PV_MAX_PROOF_BYTES` | `5242880` (5 MB; comprobantes JPG/JPEG/PNG/PDF en bucket privado) |
| `PV_VAPID_PUBLIC_KEY` | Clave pública VAPID (opcional; notificaciones del navegador) |
| `PV_VAPID_PRIVATE_KEY` | Clave privada VAPID (opcional; **solo servidor**, nunca al navegador) |
| `PV_VAPID_SUBJECT` | Identificador del remitente push (opcional; URL HTTPS o `mailto:`) |
| `PV_ADMIN_EMAIL` | Tu correo de admin (opcional, ver paso 6) |
| `PV_ADMIN_PASSWORD` | Contraseña ≥ 10 caracteres (opcional) |
| `PV_PUBLIC_BASE_URL` | Déjala vacía ahora; se rellena en el paso 7 |

> Las variables solo del servidor. Vercel no las expone al navegador. Cambiar una variable exige **redeploy** para que surta efecto.

> Las tres variables `PV_VAPID_*` son opcionales: sin ellas el panel sigue avisando de pedidos nuevos dentro de la página y el pedido no se rompe. Para activar las notificaciones del navegador, genera un par VAPID en tu PC y pega los valores **solo** en el Dashboard de Vercel; no los escribas en el chat, en GitHub ni en el código. La clave pública se entrega al navegador desde `/api/admin/push/clave`; la privada jamás sale del servidor.

---

## 5. Desplegar

1. Vercel → **Add New → Project** → importa el repositorio de GitHub.
2. Deja **Root Directory** en la raíz del repo (no lo cambies).
3. Vercel detecta el framework **FastAPI** solo (`pyproject.toml` declara las dependencias mínimas y `tool.vercel.entrypoint = "punto_vivo.app:app"`). No hay que escribir Build Command ni Start Command.
   - El `requirements.txt` de la raíz pertenece al generador y **no se instala** en Vercel. Las notificaciones push dependen de `pywebpush`, declarado en `pyproject.toml`; si falta ahí, el diagnóstico dice «Librería push: FALTA».
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
| Crear producto con precio real desde el panel | Fila nueva en `products` con `price > 0` |
| Confirmar pedido desde el menú | Filas nuevas en `orders`, `order_items` y `payments` (pago `pendiente`) |
| Marcar pagado en el panel | Pago `aprobado` y venta contabilizada una sola vez |
| Subir foto desde el panel (≤ 4 MB) | Archivo en Storage → `menu-images` |
| Escanear el QR | Abre el registro en el dominio `vercel.app` |

---

## 9. Problemas típicos en Vercel

| Síntoma | Solución |
|---|---|
| `MODULE_NOT_FOUND` / `No module named fastapi` | Revisa que `pyproject.toml` conserve las 9 dependencias (incluida `pywebpush`); Vercel prioriza ese archivo sobre `requirements.txt`. |
| Todas las rutas dan 404 | Confirma Root Directory = raíz y `tool.vercel.entrypoint = "punto_vivo.app:app"`. |
| `413 FUNCTION_PAYLOAD_TOO_LARGE` | La foto supera 4.5 MB. Reduce `PV_MAX_IMAGE_BYTES` a `4194304` y comprime la imagen antes de subirla. |
| `Origen no permitido` (403) en el panel | `PV_TRUST_PROXY=1` y redeploy. |
| El QR apunta a `127.0.0.1` | Rellena `PV_PUBLIC_BASE_URL` y descarga el QR otra vez. |
| `PV_SECRET_KEY es obligatorio...` | Añade la variable en Vercel; el disco es de solo lectura. |
| Fotos rotas | Bucket `menu-images` público y `PV_SUPABASE_BUCKET` correcto. |
| No puedo entrar al panel | Crea el admin en Supabase (paso 6); el local no se migra solo. |
| `No se pudo guardar el pedido` / RPC no encontrada | Vuelve a ejecutar **todo** `supabase_schema.sql` con **Run and enable RLS**; la RPC `crear_pedido` debe existir en `public` con 8 parámetros. |
| `Selecciona una cuenta bancaria disponible` | Completa titular y número y marca la cuenta activa en **CONFIGURACIÓN DE PAGOS**; los borradores no se publican. |
| `El comprobante debe ser JPG, JPEG, PNG o PDF` | Revisa extensión y cabecera real del archivo, y que no supere 5 MB. |
| No se ve el comprobante en el panel | El bucket `comprobantes` debe ser privado y las políticas RLS activadas; el admin lo abre desde **Pagos** o el detalle del pedido. |
| No llegan notificaciones del navegador | Activa el permiso en **CONFIGURACIÓN DE PAGOS** y configura las tres variables `PV_VAPID_*`; sin ellas queda el aviso interno. Si el diagnóstico dice «Librería push: FALTA», falta `pywebpush` en `pyproject.toml`. En iPhone abre el panel instalado desde la pantalla de inicio y, si el permiso fue rechazado, reactívalo en **Ajustes → Notificaciones → Panel PVF**. |
| `Producto no disponible o precio por definir` | Asigna precio real al producto desde **Administrar menú**; los 12 ejemplos DEMO tienen precio `0`. |
| Primera petición lenta | Arranque en frío de Vercel Functions; normal en el plan gratuito. |

---

## 10. Pagos por transferencia y avisos

1. Entra al panel → **CONFIGURACIÓN DE PAGOS** y completa los borradores de Banco Popular, BHD y Banreservas con datos reales; también puedes crear otras cuentas. Solo se muestran en el menú las cuentas activas y con titular y número completos.
2. El cliente elige **Transferencia bancaria**, ve los datos y el total exacto calculado por el servidor, confirma y puede subir su comprobante (JPG/JPEG/PNG/PDF, máx. 5 MB) desde la confirmación o desde **🧾 Mis pedidos**.
3. El archivo se guarda en el bucket privado `comprobantes`. **Subirlo no confirma el pago**: el estado pasa a `comprobante_recibido`.
4. En **Pagos** o en el detalle del pedido, el administrador abre el comprobante, verifica la transferencia y decide: `en_verificacion`, `aprobado` o `rechazado` (el rechazo pide una explicación). La confirmación queda auditada con administrador y fecha; no se puede confirmar dos veces.
5. Solo los pedidos con pago `aprobado` cuentan como venta. El cliente ve el estado y la nota en **Mis pedidos**.
6. Opcionalmente, activa las notificaciones del navegador desde **CONFIGURACIÓN DE PAGOS**. El botón emergente interno sigue funcionando aunque no haya permiso o claves VAPID.

El service worker usa la caché `pvf-static-20261126f` y atiende los eventos `push` y `notificationclick` para abrir el panel al pulsar el aviso. El servidor envía los avisos con `TTL: 3600` y `Urgency: high`: Apple descarta los mensajes con `TTL: 0` cuando la app web no está recibiendo en ese instante. En iPhone el panel debe abrirse desde la pantalla de inicio (iOS 26: «Abrir como app web» activado); el permiso se pide con `pushManager.subscribe()` dentro del toque en «Activar notificaciones», nunca desde una pestaña de Safari.

---

## 11. Qué queda igual

- En tu PC, `python -m punto_vivo` sigue usando **SQLite + `static/uploads/`** en el puerto **8790** si no defines `PV_SUPABASE_*`; los comprobantes van a `state/comprobantes/` (fuera de `/static`).
- El generador sigue en el puerto **8765** con FFmpeg, modelos y redes sociales intactos.
- El registro, el menú y la administración siguen iguales; además están las rutas de pedidos, pagos, ventas y clientes frecuentes, con las mismas validaciones en SQLite y Supabase. El borrado de una venta usa `DELETE /api/admin/pedidos/{id}` con la contraseña del administrador en el cuerpo, y exige sesión y CSRF.
