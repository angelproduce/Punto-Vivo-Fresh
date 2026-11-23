# PUNTO VIVO FRESH

Sistema web de menú digital, registro de clientes y control de visitas mediante un único código QR.
Se instaló **dentro del proyecto ANGEL PRODUCE IA existente** como un módulo independiente: no modifica ni sustituye el generador de vídeos.

> **¿Internet sin el PC encendido?** Sigue la guía [**Publicar en Vercel + Supabase**](DEPLOY_VERCEL.md) (opción elegida). Alternativa: [Render + Supabase](DEPLOY_RENDER.md). En local sigue funcionando exactamente igual.

---

## 1. Inicio rápido

```bat
:: desde la carpeta del proyecto
INICIAR PUNTO VIVO FRESH.cmd
```

Abre automáticamente `http://127.0.0.1:8790/bienvenida`.

Con doble clic en **`PUNTO VIVO FRESH.exe`** ocurre lo mismo: el EXE arranca el servidor en segundo plano y abre el navegador en el registro QR. El puerto es fijo (8790) para que el QR no cambie de dirección. Si modificas `punto_vivo_launcher.py`, regenera el EXE con `build_punto_exe.cmd`.

Sin el script:

```bat
set PYTHONPATH=%cd%\src;%cd%
.venv\Scripts\python.exe -m punto_vivo --open
```

Opciones: `--port 8790`, `--host 0.0.0.0`, `--open`.

### Acceso al panel administrativo

El primer administrador se creó con credenciales aleatorias guardadas **solo en el servidor**:

```
state\punto_vivo_acceso_inicial.txt
```

Panel: `http://127.0.0.1:8790/admin/login`

Cambiar contraseña (cierra todas las sesiones activas):

```bat
.venv\Scripts\python.exe -m punto_vivo.admin password --email admin@puntovivo.local
```

Crear otro administrador:

```bat
.venv\Scripts\python.exe -m punto_vivo.admin create --email otro@correo.com
```

También puedes crear usuarios y cambiar el correo o la contraseña desde el panel, en la sección **Usuarios**. Como ya estás autenticado, no hace falta escribir la contraseña anterior: escribe la nueva y guarda. Al cambiar tu propia contraseña se cierran tus sesiones y hay que entrar de nuevo.

En la cabecera del panel, el botón **📱 Modo móvil / 🖥️ Modo escritorio** cambia la vista del administrador y recuerda tu elección en este navegador. En un teléfono, el panel entra en modo móvil automáticamente y ocupa toda la pantalla con interfaz de app: navegación inferior fija, tarjetas en lugar de tablas, botones táctiles grandes y ventanas emergentes como hojas desde abajo. El menú público usa el mismo estilo compacto: el aviso de visita, el cambio de modo, la instalación y el botón de compartir se acomodan en filas, sin tener que deslizar.

El sitio es instalable en dos apps independientes:
- **Menú del negocio:** en el menú público pulsa **📲 Instalar app**. Al abrirla aparece el registro y el menú digital.
- **Panel administrativo:** en la pantalla de acceso o dentro del panel pulsa **📲 Instalar panel**. Al abrirla aparece el acceso; si ya tienes sesión iniciada, entra directo al panel.

Si el navegador no muestra la opción de instalación, usa su menú → «Añadir a pantalla de inicio». En **iPhone/iPad**, Apple no permite instalar una app web desde JavaScript: al pulsar **📲 Instalar app** o **📲 Instalar panel** se abre una guía en pantalla con los tres pasos (**Compartir → Añadir a pantalla de inicio → Añadir**). Ábrela siempre con **Safari**. En Android y escritorio, el botón usa la instalación nativa del navegador cuando está disponible. Puedes tener ambas apps en la pantalla de inicio del teléfono; tras instalar, cierra la ventana o haz pull-to-refresh para cargar la última versión.

El límite de acceso solo cuenta intentos fallidos: 10 contraseñas incorrectas por IP en una hora. Un inicio de sesión correcto reinicia el contador, y detrás de Vercel se usa la IP real de `X-Forwarded-For`.

---

## 2. Configuración

Copia `.env.example` a `.env` (si aún no lo hiciste) y revisa las variables `PV_*`:

| Variable | Uso |
|---|---|
| `PV_PUBLIC_BASE_URL` | **Importante antes de imprimir el QR.** Dominio público fijo, p. ej. `https://menu.tudominio.com`. El QR siempre codifica esta base + `/bienvenida`. |
| `PV_HOST` / `PV_PORT` | Dirección y puerto de escucha (`0.0.0.0:8790` por defecto). |
| `PV_ADMIN_EMAIL` / `PV_ADMIN_PASSWORD` | Si se configuran, crean el primer administrador al arrancar. |
| `PV_SECRET_KEY` | Firma de cookies de visitante. Si se omite, se genera en `state\punto_vivo_secret.key`. |
| `PV_DB_PATH` | Ruta de la base SQLite persistente (por defecto `state\punto_vivo.sqlite3`). Solo local. |
| `PV_LIMITE_IP_HORA` / `PV_LIMITE_TELEFONO_HORA` | Límites anti-abuso del formulario. |
| `PV_SUPABASE_URL` / `PV_SUPABASE_SERVICE_ROLE_KEY` | Si ambas están rellenas, la app usa Supabase en vez de SQLite (producción). La service role key **solo en el servidor**. |
| `PV_SUPABASE_BUCKET` | Bucket público de fotos (`menu-images` por defecto). |
| `PV_TRUST_PROXY` | Poner `1` detrás de Vercel/Render/proxy HTTPS para respetar `X-Forwarded-*` en cookies y origen. |
| `PV_MAX_IMAGE_BYTES` | Tamaño máximo de foto (8 MB local). En Vercel usar `4194304` por el límite de 4.5 MB por petición. |

Para publicarlo en Internet, sigue [**la guía de Render + Supabase**](DEPLOY_RENDER.md) en lugar de montar un reverse proxy a mano: el disco de Render es efímero, así que base de datos y fotos deben vivir en Supabase. El panel usa cookies `HttpOnly`, `SameSite=Lax` y token CSRF.

---

## 3. Recorrido del cliente

1. Escanea el QR → `/bienvenida`.
2. Escribe nombre y teléfono, acepta el aviso de privacidad.
3. El servidor valida, normaliza el teléfono y registra **cliente + visita en una transacción atómica**.
4. Solo si el guardado es correcto se devuelve `/menu` y se entrega una cookie firmada.
5. `/menu` y `/api/menu` rechazan el acceso sin esa cookie.

Reglas importantes:

- El cliente se identifica por **teléfono normalizado** (10 dígitos): no se crea uno nuevo al regresar.
- Cada envío válido genera una visita nueva; `visit_number` se calcula con `MAX + 1` dentro de la transacción.
- `request_token` (UUID generado en el navegador) hace idempotente el doble envío; además se ignora un segundo envío del mismo teléfono en menos de 20 segundos.
- Fecha y hora se guardan en zona `America/Santo_Domingo` (UTC-4) junto al timestamp exacto.
- Si falla el guardado, la API responde error y la página **no redirige**; el usuario puede reintentar con el mismo token.

---

## 4. Menú y administración

- **Público:** búsqueda y filtros por categoría siempre visibles (todas las categorías se muestran en filas, sin deslizar), ficha de producto, botón compartir, aviso de datos de demostración.
- **Panel:** resumen con KPIs y gráfico de 7 días, top de clientes, gestión de clientes con historial, botón **🗑 Borrar historial** (por cliente y desde su historial) y botón **🗑 Eliminar cliente** (borra el cliente y todas sus visitas), gestión de visitas con búsqueda y filtros de fecha, botón **🗑 Eliminar visitas** (borra las del filtro actual o todas) y eliminación individual, exportación CSV (clientes y visitas), CRUD de productos y categorías, activación/desactivación, orden, subida de fotos (redimensionadas a WebP). Las eliminaciones piden confirmación y no se pueden deshacer.
- **QR:** descarga PNG y SVG del mismo enlace permanente al formulario de registro.

Los productos iniciales son **12 ejemplos marcados como DEMO** con precio `0 = "Precio por definir"`. No son datos comerciales reales: reemplázalos desde *Administrar menú*. Las fotos de demostración se cargan desde Unsplash; si no hay internet, la interfaz muestra un ilustrador de respaldo por categoría. Puedes subir tus propias fotos desde el panel (se guardan optimizadas en `static/uploads/`).

Los cambios en productos/categorías se reflejan al recargar el menú público (sin caché).

---

## 5. Base de datos

Persistencia actual: **SQLite con WAL** en `state/punto_vivo.sqlite3`, con claves foráneas, restricciones `CHECK`, índices y unicidad. El registro atómico usa `BEGIN IMMEDIATE` para serializar escrituras concurrentes.

Se incluye además [`database/supabase_schema.sql`](database/supabase_schema.sql) con:

- las cinco tablas (`customers`, `visits`, `categories`, `products`, `admins`) en PostgreSQL,
- índices y restricciones,
- **RLS** activado (el público solo puede leer categorías activas y productos disponibles; datos personales y admins cerrados),
- la función `registrar_visita(...)` `SECURITY DEFINER` con bloqueo por teléfono e idempotencia,
- bucket de Storage para fotografías.

La migración ya está implementada en [`repository.py`](repository.py): con `PV_SUPABASE_URL` y `PV_SUPABASE_SERVICE_ROLE_KEY` definidas, todas las consultas y transacciones usan Supabase REST/RPC; sin esas variables, se usa SQLite local. Para migrar solo hay que ejecutar el SQL en Supabase y rellenar las variables (ver [DEPLOY_RENDER.md](DEPLOY_RENDER.md)). Nunca pongas la service role key ni la contraseña del admin en JavaScript ni en archivos públicos.

Las fotos siguen el mismo patrón: [`storage.py`](storage.py) guarda en `static/uploads/` en local y en el bucket `menu-images` de Supabase Storage en producción.

---

## 6. Seguridad y privacidad

- Contraseñas con PBKDF2-SHA256 (210 000 iteraciones) + salt aleatorio.
- Sesiones de admin en Base de datos, cookie HttpOnly y CSRF en todos los mutadores.
- Límite de intentos por IP y por teléfono (ventana de 1 hora) y campo honeypot.
- Validación en servidor de nombre, teléfono, consentimiento y token.
- Teléfonos enmascarados en el resumen público del panel (`***-***-0000`); completos solo en gestión privada y exportaciones.
- Aviso de privacidad obligatorio antes del registro.
- Sin claves de APIs ni credenciales en el navegador.

---

## 7. Pruebas

```bat
set PYTHONPATH=%cd%\src;%cd%
.venv\Scripts\python.exe -m punto_vivo.tests.run_tests
```

Cubre 33 comprobaciones: cliente nuevo/existente, incremento de visitas, fechas/horas, doble envío, redirección y bloqueo de menú, QR PNG/SVG, login y CSRF, creación/edición de productos, subida de imagen, exportación CSV, filtros y validaciones.

---

## 8. Estructura

```
punto_vivo/
  app.py                 FastAPI: rutas públicas, API y panel
  db.py                  esquema SQLite, conexiones y datos demo
  repository.py          capa de datos: SQLite local / Supabase en producción
  storage.py             fotos: local / Supabase Storage
  visits.py              validación y registro atómico de visitas
  security.py            hash, sesiones, cookies, validación y rate limit
  admin.py               CLI de administradores
  config.py              variables PV_ y zona horaria
  templates/             bienvenida, menú, login y panel
  static/css|js|uploads  interfaz y fotografías optimizadas
  database/              esquema Supabase con RLS
  tests/                 batería de pruebas
  DEPLOY_VERCEL.md       publicación en Vercel + Supabase (opción elegida)
  DEPLOY_RENDER.md       alternativa con Render + Supabase
```

Local: servidor `http://127.0.0.1:8790` · Panel `/admin` · Registro QR `/bienvenida`.
Producción: URL HTTPS de Render (configurada en `PV_PUBLIC_BASE_URL`).
