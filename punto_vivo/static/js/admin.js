/* PUNTO VIVO FRESH · panel administrativo */
(() => {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const csrf = document.querySelector('meta[name="pv-csrf"]')?.content || sessionStorage.getItem('pv_csrf') || '';
  const $ = (id) => document.getElementById(id);

  async function api(path, options = {}) {
    const opts = {...options};
    opts.headers = {...(options.headers || {}), 'X-CSRF-Token': csrf};
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!res.ok) {
      if (res.status === 401) { location.href = '/admin/login'; throw new Error('Sesión expirada'); }
      throw new Error(data.detail || 'Error en la solicitud');
    }
    return data;
  }

  function toast(text, error = false) {
    const el = $('toast');
    el.textContent = text;
    el.style.background = error ? '#a32828' : '';
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.hidden = true; }, 3200);
  }

  // En móvil cada fila se convierte en tarjeta: las celdas necesitan su etiqueta.
  function etiquetarTablas() {
    document.querySelectorAll('table.data-table').forEach((tabla) => {
      const cabeceras = Array.from(tabla.querySelectorAll('thead th')).map((th) => th.textContent.trim());
      tabla.querySelectorAll('tbody tr').forEach((fila) => {
        Array.from(fila.children).forEach((celda, indice) => {
          const etiqueta = cabeceras[indice] || '';
          if (celda.dataset.label !== etiqueta) celda.dataset.label = etiqueta;
          const ancha = !etiqueta || !!celda.querySelector('.row-actions, button, .mini-btn');
          celda.classList.toggle('cell-wide', ancha);
        });
      });
    });
  }

  // -------------------------------------------------- vista móvil / escritorio del panel
  const modeBtn = $('admin-mode-toggle');
  if (modeBtn) {
    const MODE_KEY = 'pv_admin_mode';
    const esMovil = (modo) => modo === 'mobile' || (modo === 'auto' && window.innerWidth <= 700);
    const aplicarModo = (modo) => {
      const movil = esMovil(modo);
      document.body.classList.toggle('pv-mobile', movil);
      document.body.classList.toggle('pv-desktop', modo === 'desktop');
      modeBtn.textContent = movil ? '🖥️ Modo escritorio' : '📱 Modo móvil';
      modeBtn.title = movil ? 'Cambiar a modo escritorio' : 'Cambiar a modo móvil';
      modeBtn.setAttribute('aria-pressed', String(modo !== 'auto'));
    };
    let modo = 'auto';
    try { modo = localStorage.getItem(MODE_KEY) || 'auto'; } catch (e) { /* almacenamiento bloqueado */ }
    if (modo !== 'mobile' && modo !== 'desktop') modo = 'auto';
    aplicarModo(modo);
    modeBtn.addEventListener('click', () => {
      const enMovil = document.body.classList.contains('pv-mobile') ||
        (!document.body.classList.contains('pv-desktop') && window.innerWidth <= 700);
      modo = enMovil ? 'desktop' : 'mobile';
      try { localStorage.setItem(MODE_KEY, modo); } catch (e) { /* almacenamiento bloqueado */ }
      aplicarModo(modo);
      toast(modo === 'mobile' ? 'Modo móvil activado' : 'Modo escritorio activado');
    });
    window.addEventListener('resize', () => {
      if (modo === 'auto') aplicarModo('auto');
    });
  }

  const titles = {resumen:'Resumen', clientes:'Clientes', visitas:'Visitas', menu:'Administrar menú', usuarios:'Usuarios', qr:'Código QR'};
  document.querySelectorAll('#admin-nav .nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('#admin-nav .nav-item').forEach((b) => b.classList.toggle('is-active', b === btn));
      document.querySelectorAll('.admin-section').forEach((s) => s.classList.remove('is-active'));
      const name = btn.dataset.section;
      $('section-' + name).classList.add('is-active');
      $('section-title').textContent = titles[name] || name;
      load(name);
    });
  });

  function load(name) {
    if (name === 'resumen') loadResumen();
    if (name === 'clientes') loadClientes();
    if (name === 'visitas') loadVisitas();
    if (name === 'menu') { loadCategorias(); loadProductos(); }
    if (name === 'usuarios') loadUsuarios();
    if (name === 'qr') loadQr();
  }

  async function loadResumen() {
    try {
      const d = await api('/api/admin/resumen');
      $('stat-clientes').textContent = d.total_clientes;
      $('stat-visitas').textContent = d.total_visitas;
      $('stat-hoy').textContent = d.visitas_hoy;
      $('stat-semana').textContent = d.visitas_semana;
      $('stat-mes').textContent = d.visitas_mes;
      const max = Math.max(1, ...d.serie_diaria.map((x) => x.visitas));
      $('chart-7d').innerHTML = d.serie_diaria.map((x) => {
        const label = x.fecha.slice(5).split('-').reverse().join('/');
        const h = Math.max(3, Math.round((x.visitas / max) * 160));
        return `<div class="chart-col"><div class="chart-bar" style="height:${h}px"><span>${x.visitas}</span></div><small>${label}</small></div>`;
      }).join('');
      $('top-clientes').innerHTML = d.top_clientes.length
        ? d.top_clientes.map((c) => `<tr><td><strong>${esc(c.nombre)}</strong></td><td>${esc(c.telefono)}</td><td><span class="tag">${c.total}</span></td><td>${esc(c.ultima)}</td></tr>`).join('')
        : '<tr><td colspan="4" class="muted">Aún no hay visitas registradas.</td></tr>';
      $('demo-info').innerHTML = `<span>ℹ️</span><div>El menú contiene <strong>${d.demo_productos}</strong> producto(s) de demostración y <strong>${d.productos_activos}</strong> producto(s) visibles. Reemplaza los datos demo desde «Administrar menú».</div>`;
    } catch (e) { toast(e.message, true); }
  }

  async function loadClientes() {
    try {
      const d = await api('/api/admin/clientes?q=' + encodeURIComponent($('cliente-q').value));
      $('clientes-body').innerHTML = d.clientes.length ? d.clientes.map((c) => `
        <tr><td><strong>${esc(c.nombre)}</strong></td><td>${esc(c.telefono)}</td>
        <td><span class="tag">${c.total}</span></td><td>${esc(c.primera)}</td><td>${esc(c.ultima)}</td>
        <td><div class="row-actions"><button class="mini-btn" data-history="${c.id}">Historial</button><button class="mini-btn danger" data-clear-history="${c.id}" title="Borrar todas las visitas de este cliente">🗑 Borrar historial</button></div></td></tr>`).join('')
        : '<tr><td colspan="6" class="muted">No hay clientes para ese filtro.</td></tr>';
      $('clientes-body').querySelectorAll('[data-history]').forEach((b) =>
        b.addEventListener('click', () => showHistorial(Number(b.dataset.history))));
      $('clientes-body').querySelectorAll('[data-clear-history]').forEach((b) =>
        b.addEventListener('click', () => {
          const fila = b.closest('tr');
          borrarHistorial(
            Number(b.dataset.clearHistory),
            fila?.querySelector('strong')?.textContent || 'este cliente',
            Number(fila?.querySelector('.tag')?.textContent || 0)
          );
        }));
    } catch (e) { toast(e.message, true); }
  }

  async function showHistorial(id) {
    try {
      const d = await api(`/api/admin/clientes/${id}/visitas`);
      $('history-title').textContent = `Historial · ${d.cliente.nombre}`;
      $('history-body').innerHTML = `<p class="small-note">Teléfono: ${esc(d.cliente.telefono)} · Total: ${d.visitas.length} visita(s)</p>` +
        (d.visitas.length ? d.visitas.map((v) => `<div class="visit-row"><span class="num">Visita #${v.numero}</span><div><strong>${esc(v.fecha)}</strong> · ${esc(v.hora)}<small>Registrado: ${esc(v.registro)}</small></div></div>`).join('')
          : '<p class="muted">Sin visitas.</p>') +
        `<button class="btn btn-outline btn-sm btn-danger history-clear" data-borrar-historial="${d.cliente.id}" type="button">🗑 Borrar historial de ${esc(d.cliente.nombre)}</button>`;
      $('history-backdrop').classList.remove('hidden');
    } catch (e) { toast(e.message, true); }
  }

  async function borrarHistorial(id, nombre = 'este cliente', total = null) {
    const detalle = total === null ? '' : ` Se borrarán ${total} visita(s).`;
    if (!confirm(`¿Borrar el historial de ${nombre}?${detalle} No se puede deshacer.`)) return;
    try {
      const d = await api(`/api/admin/clientes/${id}/visitas`, {method: 'DELETE'});
      toast(`Historial borrado: ${d.eliminadas} visita(s)`);
      loadClientes();
      loadResumen();
      if (!$('history-backdrop').classList.contains('hidden')) $('history-backdrop').classList.add('hidden');
    } catch (e) { toast(e.message, true); }
  }

  $('history-body').addEventListener('click', (e) => {
    const boton = e.target.closest('[data-borrar-historial]');
    if (boton) borrarHistorial(Number(boton.dataset.borrarHistorial), $('history-title').textContent.replace('Historial · ', ''));
  });

  function visitaQuery() {
    return new URLSearchParams({q: $('visita-q').value, desde: $('visita-desde').value, hasta: $('visita-hasta').value});
  }

  async function loadVisitas() {
    try {
      const params = visitaQuery();
      const d = await api('/api/admin/visitas?' + params);
      $('visitas-total').textContent = `${d.total} visita(s) en el filtro`;
      $('visitas-body').innerHTML = d.visitas.length ? d.visitas.map((v) => `
        <tr><td>${v.id}</td><td><strong>${esc(v.fecha)}</strong><br><small>${esc(v.hora)}</small></td>
        <td>${esc(v.nombre)}</td><td>${esc(v.telefono)}</td><td><span class="tag">#${v.numero}</span></td>
        <td><small>${esc(v.registro)}</small></td>
        <td><div class="row-actions"><button class="mini-btn danger" data-delete-visit="${v.id}" title="Eliminar esta visita">🗑 Eliminar</button></div></td></tr>`).join('')
        : '<tr><td colspan="7" class="muted">No hay visitas para ese filtro.</td></tr>';
      $('export-visitas').href = '/api/admin/exportar/visitas.csv?' + params;
    } catch (e) { toast(e.message, true); }
  }

  $('btn-buscar-clientes').addEventListener('click', loadClientes);
  $('cliente-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadClientes(); });
  $('btn-buscar-visitas').addEventListener('click', loadVisitas);
  ['visita-q', 'visita-desde', 'visita-hasta'].forEach((id) =>
    $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') loadVisitas(); }));
  $('export-clientes').href = '/api/admin/exportar/clientes.csv';

  // -------------------------------------------------- eliminar visitas
  $('delete-visitas').addEventListener('click', async () => {
    const params = visitaQuery();
    try {
      const actuales = await api('/api/admin/visitas?' + params);
      const conFiltro = [...params].some(([, valor]) => valor);
      const aviso = conFiltro
        ? `¿Eliminar las ${actuales.total} visita(s) del filtro actual?`
        : `¿Eliminar TODAS las ${actuales.total} visita(s) registradas?`;
      if (!confirm(`${aviso} No se puede deshacer.`)) return;
      const d = await api('/api/admin/visitas?' + params, {method: 'DELETE'});
      toast(`Visitas eliminadas: ${d.eliminadas}`);
      loadVisitas();
      loadResumen();
    } catch (e) { toast(e.message, true); }
  });

  $('visitas-body').addEventListener('click', async (e) => {
    const boton = e.target.closest('[data-delete-visit]');
    if (!boton) return;
    const fila = boton.closest('tr');
    const quien = fila?.children[2]?.textContent.trim() || 'este cliente';
    if (!confirm(`¿Eliminar la visita de ${quien}? No se puede deshacer.`)) return;
    try {
      const d = await api(`/api/admin/visitas/${boton.dataset.deleteVisit}`, {method: 'DELETE'});
      toast(`Visita eliminada (${d.eliminadas})`);
      loadVisitas();
      loadResumen();
    } catch (err) { toast(err.message, true); }
  });

  // -------------------------------------------------- categorías
  let categoriasCache = [];
  async function loadCategorias() {
    try {
      const d = await api('/api/admin/categorias');
      categoriasCache = d.categorias;
      $('cat-body').innerHTML = d.categorias.map((c) => `
        <tr><td><strong>${esc(c.name)}</strong><br><small>${esc(c.description)}</small></td>
        <td>${c.productos}</td><td>${c.sort_order}</td>
        <td><span class="tag ${c.active ? '' : 'off'}">${c.active ? 'Activa' : 'Inactiva'}</span></td>
        <td><div class="row-actions"><button class="mini-btn" data-cat-edit="${c.id}">Editar</button>
        <button class="mini-btn danger" data-cat-del="${c.id}">Eliminar</button></div></td></tr>`).join('');
      $('cat-body').querySelectorAll('[data-cat-edit]').forEach((b) =>
        b.addEventListener('click', () => editarCategoria(Number(b.dataset.catEdit))));
      $('cat-body').querySelectorAll('[data-cat-del]').forEach((b) =>
        b.addEventListener('click', async () => {
          if (!confirm('¿Eliminar esta categoría?')) return;
          try { await api('/api/admin/categorias/' + b.dataset.catDel, {method:'DELETE'}); toast('Categoría eliminada'); loadCategorias(); loadProductos(); }
          catch (e) { toast(e.message, true); }
        }));
      const select = $('prod-cat');
      select.innerHTML = d.categorias.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join('');
    } catch (e) { toast(e.message, true); }
  }

  function editarCategoria(id) {
    const c = categoriasCache.find((x) => x.id === id);
    if (!c) return;
    $('cat-edit-id').value = c.id;
    $('cat-nombre').value = c.name;
    $('cat-desc').value = c.description;
    $('cat-orden').value = c.sort_order;
    $('cat-activa').checked = !!c.active;
    $('cat-submit').textContent = 'Guardar cambios';
    $('cat-cancel').classList.remove('hidden');
    $('cat-nombre').focus();
  }

  function resetCategoria() {
    $('cat-form').reset();
    $('cat-edit-id').value = '';
    $('cat-orden').value = 0;
    $('cat-activa').checked = true;
    $('cat-submit').textContent = 'Crear categoría';
    $('cat-cancel').classList.add('hidden');
  }

  $('cat-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {name: $('cat-nombre').value, description: $('cat-desc').value,
      sort_order: Number($('cat-orden').value || 0), active: $('cat-activa').checked, image: ''};
    const id = $('cat-edit-id').value;
    try {
      await api(id ? '/api/admin/categorias/' + id : '/api/admin/categorias',
        {method: id ? 'PUT' : 'POST', body: payload});
      toast(id ? 'Categoría actualizada' : 'Categoría creada');
      resetCategoria(); loadCategorias();
    } catch (err) { toast(err.message, true); }
  });
  $('cat-cancel').addEventListener('click', resetCategoria);

  // -------------------------------------------------- productos
  async function loadProductos() {
    try {
      const d = await api('/api/admin/productos');
      $('productos-body').innerHTML = d.productos.length ? d.productos.map((p) => `
        <tr>
          <td>${p.image ? `<img class="product-thumb" src="${esc(p.image)}" alt="" loading="lazy">` : '<div class="product-thumb"></div>'}</td>
          <td><strong>${esc(p.name)}</strong><br>${p.is_demo ? '<span class="tag demo">DEMO</span>' : ''}</td>
          <td><small>${esc(p.categoria)}</small></td>
          <td>${p.is_demo && Number(p.price) === 0 ? '<small>Por definir</small>' : 'RD$ ' + Number(p.price).toLocaleString('es-DO', {minimumFractionDigits:2})}</td>
          <td><button class="mini-btn" data-toggle="${p.id}" data-value="${p.available}">${p.available ? 'Visible' : 'Oculto'}</button></td>
          <td><div class="row-actions">
            <button class="mini-btn" data-up="${p.id}" title="Subir en el orden">↑</button>
            <button class="mini-btn" data-down="${p.id}" title="Bajar en el orden">↓</button>
            <button class="mini-btn" data-prod-edit="${p.id}">Editar</button>
            <button class="mini-btn danger" data-prod-del="${p.id}">Eliminar</button>
          </div></td>
        </tr>`).join('') : '<tr><td colspan="6" class="muted">No hay productos. Crea el primero.</td></tr>';

      const body = $('productos-body');
      body.querySelectorAll('[data-toggle]').forEach((b) => b.addEventListener('click', async () => {
        try { await api(`/api/admin/productos/${b.dataset.toggle}/estado`, {method:'PATCH', body:{available: b.dataset.value !== '1'}}); loadProductos(); }
        catch (e) { toast(e.message, true); }
      }));
      body.querySelectorAll('[data-up]').forEach((b) => b.addEventListener('click', () => mover(Number(b.dataset.up), -1)));
      body.querySelectorAll('[data-down]').forEach((b) => b.addEventListener('click', () => mover(Number(b.dataset.down), 1)));
      body.querySelectorAll('[data-prod-edit]').forEach((b) => b.addEventListener('click', () => openProducto(d.productos.find((p) => p.id === Number(b.dataset.prodEdit)))));
      body.querySelectorAll('[data-prod-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('¿Eliminar este producto?')) return;
        try { await api('/api/admin/productos/' + b.dataset.prodDel, {method:'DELETE'}); toast('Producto eliminado'); loadProductos(); }
        catch (e) { toast(e.message, true); }
      }));
    } catch (e) { toast(e.message, true); }
  }

  async function mover(id, delta) {
    const list = await api('/api/admin/productos');
    const p = list.productos.find((x) => x.id === id);
    if (!p) return;
    try { await api(`/api/admin/productos/${id}/orden`, {method:'PATCH', body:{sort_order: Number(p.sort_order) + delta}}); loadProductos(); }
    catch (e) { toast(e.message, true); }
  }

  function openProducto(p) {
    $('product-form').reset();
    $('prod-id').value = p ? p.id : '';
    $('prod-nombre').value = p ? p.name : '';
    $('prod-cat').value = p ? p.category_id : (categoriasCache[0]?.id || '');
    $('prod-desc').value = p ? p.description : '';
    $('prod-precio').value = p ? p.price : 0;
    $('prod-imagen').value = p ? p.image : '';
    $('prod-orden').value = p ? p.sort_order : 0;
    $('prod-available').checked = p ? !!p.available : true;
    $('prod-demo').checked = p ? !!p.is_demo : false;
    $('product-modal-title').textContent = p ? 'Editar producto' : 'Nuevo producto';
    $('product-backdrop').classList.remove('hidden');
  }

  $('btn-nuevo-producto').addEventListener('click', () => openProducto(null));
  $('product-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = $('prod-id').value;
    const payload = {
      category_id: Number($('prod-cat').value), name: $('prod-nombre').value,
      description: $('prod-desc').value, price: Number($('prod-precio').value || 0),
      image: $('prod-imagen').value, available: $('prod-available').checked,
      is_demo: $('prod-demo').checked, sort_order: Number($('prod-orden').value || 0)
    };
    const submit = $('prod-submit');
    submit.disabled = true;
    try {
      const saved = await api(id ? '/api/admin/productos/' + id : '/api/admin/productos',
        {method: id ? 'PUT' : 'POST', body: payload});
      const productId = id || saved.id;
      const file = $('prod-file').files[0];
      if (file) {
        const fd = new FormData();
        fd.append('file', file);
        const uploaded = await api(`/api/admin/productos/${productId}/imagen`, {method:'POST', body: fd});
        $('prod-imagen').value = uploaded.image;
      }
      toast(id ? 'Producto actualizado' : 'Producto creado');
      $('product-backdrop').classList.add('hidden');
      loadProductos();
    } catch (err) { toast(err.message, true); }
    finally { submit.disabled = false; }
  });

  // -------------------------------------------------- usuarios y contraseñas
  const miId = Number(document.querySelector('meta[name="pv-admin-id"]')?.content || 0);
  let usuariosCache = [];

  async function loadUsuarios() {
    try {
      const d = await api('/api/admin/usuarios');
      usuariosCache = d.usuarios || [];
      $('usuarios-body').innerHTML = usuariosCache.map((u) => `
        <tr>
          <td><strong>${esc(u.email)}</strong>${Number(u.id) === miId ? ' <span class="tag">Tú</span>' : ''}</td>
          <td><span class="tag ${u.role === 'admin' ? '' : 'off'}">${esc(u.role)}</span></td>
          <td>${esc(String(u.created_at || '').slice(0, 10))}</td>
          <td><div class="row-actions">
            <button class="mini-btn" data-user-edit="${u.id}">Editar</button>
            <button class="mini-btn" data-user-pass="${u.id}">Contraseña</button>
          </div></td>
        </tr>`).join('') || '<tr><td colspan="4">No hay usuarios.</td></tr>';
      document.querySelectorAll('[data-user-edit]').forEach((b) =>
        b.addEventListener('click', () => openUsuario(Number(b.dataset.userEdit))));
      document.querySelectorAll('[data-user-pass]').forEach((b) =>
        b.addEventListener('click', () => openUsuario(Number(b.dataset.userPass), true)));
    } catch (e) { toast(e.message, true); }
  }

  function openUsuario(id = 0, soloClave = false) {
    const fila = usuariosCache.find((u) => Number(u.id) === Number(id));
    $('user-id').value = id || '';
    $('user-email').value = id ? (fila?.email || '') : '';
    $('user-email').disabled = soloClave;
    $('user-password').value = '';
    $('user-password').required = !id;
    $('user-password-title').textContent = id ? 'Nueva contraseña' : 'Contraseña';
    $('user-password-note').textContent = id
      ? 'Mínimo 10 caracteres. Déjala vacía para conservar la contraseña actual.'
      : 'Mínimo 10 caracteres. La necesitarás para entrar al panel.';
    $('user-modal-title').textContent = !id ? 'Nuevo usuario' : (soloClave ? 'Cambiar contraseña' : 'Editar usuario');
    $('user-submit').textContent = !id ? 'Crear usuario' : 'Guardar cambios';
    $('user-backdrop').classList.remove('hidden');
    setTimeout(() => $(soloClave ? 'user-password' : 'user-email').focus(), 0);
  }

  $('btn-nuevo-usuario').addEventListener('click', () => openUsuario());
  $('user-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = $('user-submit');
    const id = $('user-id').value;
    const email = $('user-email').value.trim();
    const password = $('user-password').value;
    if (id && password && password.length < 10) {
      toast('La contraseña debe tener al menos 10 caracteres.', true); return;
    }
    submit.disabled = true;
    try {
      if (!id) {
        await api('/api/admin/usuarios', {method:'POST', body:{email, password}});
      } else {
        const body = {email};
        if (password) {
          body.password = password;
        }
        const resultado = await api('/api/admin/usuarios/' + id, {method:'PUT', body});
        if (resultado.cerrar_sesion) {
          sessionStorage.setItem('pv_login_msg', 'Contraseña actualizada. Inicia sesión con la nueva contraseña.');
          sessionStorage.removeItem('pv_csrf');
          location.href = '/admin/login';
          return;
        }
      }
      $('user-backdrop').classList.add('hidden');
      toast(id ? 'Usuario actualizado' : 'Usuario creado');
      loadUsuarios();
    } catch (err) { toast(err.message, true); }
    finally { submit.disabled = false; }
  });

  document.querySelectorAll('[data-close]').forEach((btn) =>
    btn.addEventListener('click', () => $(btn.dataset.close).classList.add('hidden')));
  document.querySelectorAll('.modal-backdrop').forEach((backdrop) =>
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) backdrop.classList.add('hidden'); }));

  // -------------------------------------------------- QR
  async function loadQr() {
    try {
      const d = await api('/api/admin/qr/info');
      $('qr-url').value = d.url;
      $('qr-nota').innerHTML = `<span>✅</span><div><strong>${esc(d.nota)}</strong><br>${esc(d.destino)}</div>`;
    } catch (e) { toast(e.message, true); }
  }
  $('btn-copiar-url').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('qr-url').value); toast('Enlace del QR copiado'); }
    catch (e) { $('qr-url').select(); document.execCommand('copy'); toast('Enlace copiado'); }
  });

  const salir = async () => {
    try { await api('/api/admin/logout', {method:'POST'}); } catch (e) { /* ignore */ }
    sessionStorage.removeItem('pv_csrf');
    location.href = '/admin/login';
  };
  $('btn-logout').addEventListener('click', salir);
  $('btn-logout-movil')?.addEventListener('click', salir);

  // Las tablas se redibujan con innerHTML: vuelve a etiquetarlas al cambiar el contenido.
  const observadorTablas = new MutationObserver(etiquetarTablas);
  document.querySelectorAll('table.data-table tbody').forEach((tbody) =>
    observadorTablas.observe(tbody, {childList: true}));
  etiquetarTablas();

  load('resumen');
})();
