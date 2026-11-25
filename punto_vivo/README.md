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

En la cabecera del panel, el botón **📱 Modo móvil / 🖥️ Modo escritorio** cambia la vista del administrador y recuerda tu elección en este navegador. En un teléfono, el panel entra en modo móvil automáticamente y ocupa toda la pantalla con interfaz de app: navegación inferior fija y deslizable, tarjetas en lugar de tablas, botones táctiles grandes y ventanas emergentes como hojas desde abajo. El menú público usa el mismo estilo compacto: el aviso de visita, el cambio de modo, la instalación y el botón de compartir se acomodan en filas, sin tener que deslizar.

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
| `PV_MAX_PROOF_BYTES` | Tamaño máximo del comprobante de transferencia (`5242880` = 5 MB). |
| `PV_VAPID_PUBLIC_KEY` / `PV_VAPID_PRIVATE_KEY` / `PV_VAPID_SUBJECT` | Par VAPID para notificaciones del navegador al admin. Opcionales: en local se usa `state/vapid.json` como respaldo; la privada nunca se envía al navegador. |

Para publicarlo en Internet, sigue [**la guía de Render + Supabase**](DEPLOY_RENDER.md) en lugar de montar un reverse proxy a mano: el disco de Render es efímero, así que base de datos y fotos deben vivir en Supabase. El panel usa cookies `HttpOnly`, `SameSite=Lax` y token CSRF.

---

## 3. Recorrido del cliente

1. Escanea el QR → `/bienvenida`.
2. Escribe nombre y teléfono, acepta el aviso de privacidad.
3. El servidor valida, normaliza el teléfono y registra **cliente + visita en una transacción atómica**.
4. Solo si el guardado es correcto se devuelve `/menu` y se entrega una cookie firmada.
5. `/menu` y `/api/menu` rechazan el acceso sin esa cookie.
6. En el menú elige productos, ajusta cantidades, escribe observaciones y confirma el pedido.
7. El servidor valida disponibilidad y precios de nuevo, guarda el pedido con un número único y lo deja pendiente de pago.

Reglas importantes:

- El cliente se identifica por **teléfono normalizado** (10 dígitos): no se crea uno nuevo al regresar.
- Cada envío válido genera una visita nueva; `visit_number` se calcula con `MAX + 1` dentro de la transacción.
- `request_token` (UUID generado en el navegador) hace idempotente el doble envío; además se ignora un segundo envío del mismo teléfono en menos de 20 segundos.
- Los pedidos usan su propio `request_token`: un doble clic en **Confirmar pedido** no crea dos pedidos.
- **Las visitas no son compras.** El historial de visitas y el historial de pedidos son independientes.
- Fecha y hora se guardan en zona `America/Santo_Domingo` (UTC-4) junto al timestamp exacto.
- Si falla el guardado, la API responde error y la página **no redirige**; el usuario puede reintentar con el mismo token.

---

## 4. Menú, carrito y panel administrativo

- **Público:** búsqueda y filtros por categoría siempre visibles (todas las categorías se muestran en filas, sin deslizar), tarjetas compactas con el precio sobre los botones «Ver detalle» y «🛒 Agregar», ficha de producto, botón compartir, carrito persistente con cantidades y observaciones por producto, checkout con entrega/retiro, dirección, método de pago y observaciones generales, confirmación con número de pedido y aviso de datos de demostración, y botón **🚪 Cerrar sesión** que borra la cookie del visitante y limpia el carrito local para que otra persona pueda registrarse.
- **Panel:** resumen con KPIs de visitas **y ventas**, gestión de pedidos (filtrar, ver detalle, aceptar, preparar, marcar listo/entregado, rechazar o cancelar, confirmar pago, exportar CSV y **Borrar pedido** con contraseña), secciones **Pagos** (transferencias, comprobantes, revisión con nota y **Borrar comprobante** con contraseña) y **CONFIGURACIÓN DE PAGOS** (cuentas bancarias y notificaciones), sección **Ventas** (ingresos de 7 días, situación de cobros, productos más vendidos, ingresos por categoría e historial con botón **Borrar venta** que pide la contraseña del administrador sesionado), sección **Clientes frecuentes** (compras pagadas, total y promedio), perfil del cliente (visitas, pedidos, gasto y favoritos), notificación de pedidos nuevos con alarma sonora fuerte, top de clientes, gestión de visitas, exportaciones CSV, CRUD de productos y categorías, activación/desactivación, orden y subida de fotos (redimensionadas a WebP). Las eliminaciones piden confirmación y no se pueden deshacer.
- **QR:** descarga PNG y SVG del mismo enlace permanente al formulario de registro.

Los productos iniciales son **12 ejemplos marcados como DEMO** con precio `0 = "Precio por definir"`. No son datos comerciales reales: reemplázalos desde *Administrar menú* y asigna precios reales. Los productos sin precio **no se pueden agregar al carrito** ni pedir. Las fotos de demostración se cargan desde Unsplash; si no hay internet, la interfaz muestra un ilustrador de respaldo por categoría. Puedes subir tus propias fotos desde el panel (se guardan optimizadas en `static/uploads/`).

Los cambios en productos/categorías se reflejan al recargar el menú público (sin caché). El carrito se revalida contra `/api/menu` antes de confirmar: si un producto dejó de estar disponible o cambió de precio, se actualiza o se retira con un aviso.

---

## 5. Pedidos, cobros y ventas

- El cliente elige entre **efectivo** y **transferencia bancaria**; «tarjeta» ya no está disponible. **No puede marcarlo como pagado**: el pago queda `pendiente` hasta que el administrador verifique y confirme el cobro en el panel.
- Para transferir, el negocio configura sus cuentas en **CONFIGURACIÓN DE PAGOS**. Los borradores iniciales (Banco Popular, BHD y Banreservas) no tienen titular ni número inventados: solo se muestran al cliente cuando están completos y activos.
- El cliente puede subir un comprobante JPG/JPEG/PNG/PDF de hasta **5 MB** justo al confirmar el pedido —lo selecciona y pulsa **Listo** para enviarlo— o después desde **🧾 Mis pedidos**. El archivo se guarda en almacenamiento privado (`state/comprobantes/` en local, bucket privado `comprobantes` en Supabase) y solo lo lee el administrador mediante una URL firmada o la API.
- **Subir una imagen nunca confirma el pago**: pasa a `comprobante_recibido`. El administrador puede ponerlo `en_verificacion`, `aprobado` o `rechazado`, siempre con sesión y CSRF. Se guarda quién confirmó (`confirmed_by`) y cuándo (`confirmed_at`), y la doble confirmación se bloquea.
- El administrador puede **borrar un comprobante** desde la fila del pedido o desde la vista del archivo; se pide su contraseña, se elimina el archivo privado y su registro, y el estado del pago **no cambia** (debe revisarlo de nuevo antes de confirmar). Borrar un pedido elimina también su comprobante. La contraseña incorrecta devuelve `400` y repetir la eliminación devuelve `404`.
- Una venta definitiva es un pedido con pago `aprobado` y estado distinto de `rechazada`/`cancelada`. Si se rechaza o cancela un pedido ya pagado, el pago pasa a `reembolsado` y deja de contar.
- El resumen muestra por separado pedidos nuevos, pendientes de pago, ventas del día/semana/mes y monto pendiente.
- El mismo pedido se contabiliza **una sola vez**; los pedidos pendientes, rechazados y cancelados nunca suman ingresos.
- `discount` y `delivery_fee` existen en la base con valor `0` por defecto; no se aplican cargos automáticos. El total siempre lo calcula el servidor con precios propios; el navegador no puede modificarlo.
- Los estados de pedido son `pendiente`, `aceptada`, `preparando`, `lista`, `entregada`, `rechazada` y `cancelada`.
- Los estados de pago son `pendiente`, `comprobante_recibido`, `en_verificacion`, `aprobado`, `rechazado` y `reembolsado`. Solo `aprobado` cuenta como venta.
- Los clientes ven **solo sus pedidos** desde su cookie firmada; no existe una ruta pública para consultar pedidos por ID ajeno. En «Mis pedidos» ven el estado del pago y la nota de la revisión, sin correos externos.
- El panel avisa de pedidos nuevos con el aviso interno y, si se activa el permiso y hay claves VAPID, con una **notificación del navegador**. El aviso es un extra: un fallo de notificación nunca invalida un pedido.
- No se puede eliminar un cliente ni un producto que aparece en pedidos (la API devuelve `409`).
- **Borrar una venta:** en el historial de **Ventas**, el botón 🗑 pide la contraseña del administrador y elimina el pedido completo (pedido, artículos y pago). Sin la contraseña correcta no se borra nada; la petición requiere sesión admin y CSRF. Al borrarse, el pedido desaparece de Pedidos y Ventas y el resumen se recalcula. La contraseña nunca se envía por chat ni se guarda en el navegador.

---

## 6. Base de datos

Persistencia local: **SQLite con WAL** en `state/punto_vivo.sqlite3`, con claves foráneas, restricciones `CHECK`, índices y unicidad. El registro atómico y la creación de pedidos usan `BEGIN IMMEDIATE` para serializar escrituras concurrentes.

Se incluye además [`database/supabase_schema.sql`](database/supabase_schema.sql) con:

- las tablas `customers`, `visits`, `categories`, `products`, `admins`, `delivery_addresses`, `orders`, `order_items`, `payments`, `payment_accounts`, `payment_proofs` y `admin_push_subscriptions`,
- índices y restricciones, auditoría de pagos (`reviewed_by`, `reviewed_at`, `confirmed_by`, `confirmed_at`) y `bank_account_id` en pedidos/comprobantes,
- **RLS** activado (el público solo puede leer categorías activas y productos disponibles; datos personales, pedidos, comprobantes y admins cerrados),
- las funciones `registrar_visita(...)` y `crear_pedido(...)` `SECURITY DEFINER` con idempotencia, validación de precios en servidor y cuenta bancaria obligatoria en transferencias,
- bucket público `menu-images` para fotografías y bucket **privado** `comprobantes` para los comprobantes.

La migración ya está implementada en [`repository.py`](repository.py): con `PV_SUPABASE_URL` y `PV_SUPABASE_SERVICE_ROLE_KEY` definidas, todas las consultas y transacciones usan Supabase REST/RPC; sin esas variables, se usa SQLite local. **Antes de publicar los pedidos hay que volver a ejecutar todo el SQL en Supabase** (ver [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md)) para crear tablas y la RPC `crear_pedido`. Nunca pongas la service role key ni la contraseña del admin en JavaScript ni en archivos públicos.

Las fotos siguen el mismo patrón: [`storage.py`](storage.py) guarda en `static/uploads/` en local y en el bucket `menu-images` de Supabase Storage en producción. Los comprobantes, en cambio, se guardan en `state/comprobantes/` en local y en el bucket privado `comprobantes` en producción; el servidor genera una URL firmada de corta duración solo para el administrador autorizado.

Al actualizar la app hay que **volver a ejecutar todo el SQL** con **Run and enable RLS** (nunca «Run without RLS») antes de publicar: la RPC antigua de 7 parámetros se sustituye por la de 8 con `p_bank_account_id`.

---

## 7. Seguridad y privacidad

- Contraseñas con PBKDF2-SHA256 (210 000 iteraciones) + salt aleatorio.
- Sesiones de admin en Base de datos, cookie HttpOnly y CSRF en todos los mutadores.
- Confirmación de pago restringida al panel: exige sesión de admin y CSRF; el navegador del cliente no puede llamarla. La revisión de transferencias exige comprobante y guarda auditoría; subir la imagen no basta para aprobar.
- Comprobantes privados: nunca se sirven desde `/static`; en producción se entregan con URLs firmadas por el servidor y solo a sesiones de administrador.
- Límite de intentos por IP y por teléfono (ventana de 1 hora) y campo honeypot.
- Validación en servidor de nombre, teléfono, consentimiento, token, cantidades y precios; el total del navegador nunca se confía.
- Teléfonos enmascarados en el resumen público del panel (`***-***-0000`); completos solo en gestión privada y exportaciones.
- Aviso de privacidad obligatorio antes del registro.
- Sin claves de APIs ni credenciales en el navegador.

---

## 8. Pruebas

```bat
set PYTHONPATH=%cd%\src;%cd%
.venv\Scripts\python.exe -m punto_vivo.tests.run_tests
```

Cubre 158 comprobaciones: cliente nuevo/existente, visitas, fechas/horas, doble envío, menú y QR, login/CSRF/bloqueo, productos, imágenes, CSV, PWA, carrito y checkout, validación de precios, idempotencia de pedidos, aislamiento entre clientes, estados, pago del administrador, ventas, rechazos, borrado de pedidos y ventas con contraseña, borrado de comprobantes con contraseña, perfiles, frecuentes, cierre de sesión del cliente, protección de datos y el flujo completo de transferencias (cuentas, comprobantes privados, estados de pago, auditoría, doble confirmación y notificaciones).

---

## 9. Estructura

```
punto_vivo/
  app.py                 FastAPI: rutas públicas, API y panel
  db.py                  esquema SQLite, conexiones y datos demo
  repository.py          capa de datos: SQLite local / Supabase en producción
  storage.py             fotos y comprobantes privados: local / Supabase Storage
  push.py                notificaciones Web Push al administrador
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
Producción: `https://punto-vivo-fresh.vercel.app` (configurada en `PV_PUBLIC_BASE_URL`).
