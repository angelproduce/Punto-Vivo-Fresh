/* PUNTO VIVO FRESH · panel administrativo */
(() => {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (v) => 'RD$ ' + Number(v || 0).toLocaleString('es-DO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
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

  const titles = {resumen:'Resumen', pedidos:'Pedidos', ventas:'Ventas', frecuentes:'Clientes frecuentes', clientes:'Clientes', visitas:'Visitas', menu:'Administrar menú', usuarios:'Usuarios', qr:'Código QR'};
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
    if (name === 'pedidos') loadPedidos();
    if (name === 'ventas') loadVentas();
    if (name === 'frecuentes') loadFrecuentes();
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
      $('stat-pedidos-nuevos').textContent = d.pedidos_pendientes;
      $('stat-ventas-hoy').textContent = money(d.ventas_hoy);
      $('stat-ventas-mes').textContent = money(d.ventas_mes);
      $('stat-pendiente-pago').textContent = money(d.monto_pendiente);
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

  // -------------------------------------------------- pedidos y notificaciones
  const ESTADOS = {
    pendiente:'Pendiente', aceptada:'Aceptada', preparando:'Preparando', lista:'Lista',
    entregada:'Entregada', rechazada:'Rechazada', cancelada:'Cancelada',
  };
  const PAGOS = {pendiente:'Pendiente', aprobado:'Aprobado', rechazado:'Rechazado', reembolsado:'Reembolsado'};
  const badge = (valor, mapa) => `<span class="badge badge-${esc(valor)}">${esc(mapa[valor] || valor)}</span>`;

  function pedidoQuery() {
    return new URLSearchParams({
      q: $('pedido-q').value, estado: $('pedido-estado').value, pago: $('pedido-pago').value,
      desde: $('pedido-desde').value, hasta: $('pedido-hasta').value,
    });
  }

  function accionesPedido(p) {
    let html = `<button class="mini-btn" data-ver-pedido="${p.id}">Ver detalle</button>`;
    if (p.estado === 'pendiente') {
      html += `<button class="mini-btn" data-id="${p.id}" data-accion="aceptar">Aceptar</button>`;
      html += `<button class="mini-btn danger" data-id="${p.id}" data-accion="rechazar">Rechazar</button>`;
    } else if (p.estado === 'aceptada') {
      html += `<button class="mini-btn" data-id="${p.id}" data-accion="preparando">Preparando</button>`;
    } else if (p.estado === 'preparando') {
      html += `<button class="mini-btn" data-id="${p.id}" data-accion="lista">Listo</button>`;
    } else if (p.estado === 'lista') {
      html += `<button class="mini-btn" data-id="${p.id}" data-accion="entregada">Entregado</button>`;
    }
    if (p.pago_estado === 'pendiente') {
      html += `<button class="mini-btn pay" data-pagar="${p.id}">Marcar pagado</button>`;
    } else if (p.pago_estado === 'aprobado') {
      html += `<button class="mini-btn" data-despagar="${p.id}">Quitar pago</button>`;
    }
    if (!['entregada', 'rechazada', 'cancelada'].includes(p.estado)) {
      html += `<button class="mini-btn danger" data-id="${p.id}" data-accion="cancelar">Cancelar</button>`;
    }
    return html;
  }

  async function loadPedidos() {
    try {
      const params = pedidoQuery();
      const d = await api('/api/admin/pedidos?' + params);
      $('pedidos-total').textContent = `${d.total} pedido(s) en el filtro`;
      $('export-pedidos').href = '/api/admin/exportar/pedidos.csv?' + params;
      const lista = d.pedidos;
      $('ped-stat-nuevos').textContent = lista.filter((p) => p.estado === 'pendiente').length;
      $('ped-stat-pendientes').textContent = lista.filter((p) => p.pago_estado === 'pendiente').length;
      $('ped-stat-preparando').textContent = lista.filter((p) => ['preparando', 'lista'].includes(p.estado)).length;
      $('ped-stat-entregados').textContent = lista.filter((p) => p.estado === 'entregada').length;
      $('pedidos-body').innerHTML = lista.length ? lista.map((p) => `
        <tr><td><strong>${esc(p.numero)}</strong></td><td>${esc(p.cliente)}</td>
        <td>${esc(p.telefono)}</td><td>${p.entrega === 'delivery' ? 'Domicilio' : 'Retiro'}</td>
        <td><strong>${money(p.total)}</strong></td><td>${badge(p.estado, ESTADOS)}</td>
        <td>${badge(p.pago_estado, PAGOS)}</td><td><small>${esc(String(p.creado).replace('T', ' ').slice(0, 16))}</small></td>
        <td><div class="row-actions">${accionesPedido(p)}</div></td></tr>`).join('')
        : '<tr><td colspan="9" class="muted">No hay pedidos para ese filtro.</td></tr>';
    } catch (e) { toast(e.message, true); }
  }

  async function showPedido(id) {
    try {
      const p = await api('/api/admin/pedidos/' + id);
      $('order-title').textContent = 'Pedido ' + p.numero;
      let html = `<div class="order-meta">
        <div><small>Cliente</small><strong>${esc(p.cliente)}</strong></div>
        <div><small>Teléfono</small><strong>${esc(p.telefono)}</strong></div>
        <div><small>Estado</small>${badge(p.estado, ESTADOS)}</div>
        <div><small>Pago</small>${badge(p.pago ? p.pago.estado : 'pendiente', PAGOS)}</div>
        <div><small>Entrega</small><strong>${p.entrega === 'delivery' ? 'A domicilio' : 'Recoger en el negocio'}</strong></div>
        <div><small>Fecha</small><strong>${esc(String(p.creado).replace('T', ' ').slice(0, 19))}</strong></div>
      </div>`;
      if (p.direccion) {
        html += `<div class="notice notice-info"><span>📍</span><div><strong>Dirección de entrega</strong><br>${esc(p.direccion.linea1)} ${esc(p.direccion.linea2)}<br>${esc(p.direccion.ciudad)} ${esc(p.direccion.provincia)} ${esc(p.direccion.notas)}</div></div>`;
      }
      if (p.notas) html += `<div class="notice notice-warn"><span>📝</span><div><strong>Observaciones</strong><br>${esc(p.notas)}</div></div>`;
      html += `<h4>Productos del pedido</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>Producto</th><th>Precio</th><th>Cant.</th><th>Observación</th><th>Subtotal</th></tr></thead><tbody>`;
      html += (p.items || []).map((i) => `<tr><td>${esc(i.nombre)}</td><td>${money(i.precio)}</td><td>${i.cantidad}</td><td>${esc(i.notas || '—')}</td><td><strong>${money(i.subtotal)}</strong></td></tr>`).join('');
      html += `</tbody></table></div><div class="order-totals"><div><span>Subtotal</span><strong>${money(p.subtotal)}</strong></div><div><span>Descuento</span><strong>${money(p.descuento)}</strong></div><div><span>Envío</span><strong>${money(p.envio)}</strong></div><div class="grand"><span>Total</span><strong>${money(p.total)}</strong></div></div>`;
      if (p.pago) html += `<p class="small-note">Método de pago elegido: ${esc(p.pago.metodo)}. Confirmado por el administrador ${p.pago.pagado_en ? esc(String(p.pago.pagado_en).slice(0, 16)) : '—'}.</p>`;
      $('order-body').innerHTML = html;
      $('order-foot').innerHTML = `<div class="row-actions">${accionesPedido(p)}</div>`;
      $('order-backdrop').classList.remove('hidden');
    } catch (e) { toast(e.message, true); }
  }

  async function accionPedido(id, accion) {
    const nombres = {aceptar:'aceptar', rechazar:'rechazar', preparando:'marcar como preparando',
      lista:'marcar como listo', entregada:'marcar como entregado', cancelar:'cancelar'};
    if (['rechazar', 'cancelar'].includes(accion) &&
        !confirm(`¿Seguro que deseas ${nombres[accion] || accion} este pedido?`)) return;
    try {
      await api(`/api/admin/pedidos/${id}/estado`, {method: 'PATCH', body: {accion}});
      toast('Estado del pedido actualizado');
      $('order-backdrop').classList.add('hidden');
      loadPedidos(); loadResumen(); loadVentas(); checkPedidosNuevos();
    } catch (e) { toast(e.message, true); }
  }

  async function marcarPago(id, pagado) {
    const aviso = pagado
      ? 'Confirma que recibiste el pago de este pedido. La venta se contabilizará como definitiva.'
      : '¿Dejar este pago como pendiente? La venta dejará de contabilizarse.';
    if (!confirm(aviso)) return;
    try {
      await api(`/api/admin/pedidos/${id}/pago`, {method: 'PATCH', body: {pagado, confirmado: true}});
      toast(pagado ? 'Pago confirmado' : 'Pago marcado como pendiente');
      $('order-backdrop').classList.add('hidden');
      loadPedidos(); loadResumen(); loadVentas(); loadFrecuentes(); checkPedidosNuevos();
    } catch (e) { toast(e.message, true); }
  }

  document.addEventListener('click', (event) => {
    const ver = event.target.closest('[data-ver-pedido]');
    if (ver) { showPedido(Number(ver.dataset.verPedido)); return; }
    const accion = event.target.closest('[data-accion]');
    if (accion) { accionPedido(Number(accion.dataset.id), accion.dataset.accion); return; }
    const pagar = event.target.closest('[data-pagar]');
    if (pagar) { marcarPago(Number(pagar.dataset.pagar), true); return; }
    const despagar = event.target.closest('[data-despagar]');
    if (despagar) marcarPago(Number(despagar.dataset.despagar), false);
  });

  $('btn-buscar-pedidos').addEventListener('click', loadPedidos);
  $('btn-refrescar-pedidos').addEventListener('click', () => {
    ['pedido-q', 'pedido-desde', 'pedido-hasta'].forEach((id) => { $(id).value = ''; });
    $('pedido-estado').value = '';
    $('pedido-pago').value = '';
    loadPedidos();
  });
  ['pedido-q', 'pedido-desde', 'pedido-hasta'].forEach((id) =>
    $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') loadPedidos(); }));

  async function checkPedidosNuevos() {
    try {
      const d = await api('/api/admin/pedidos/nuevos');
      const badgeEl = $('pedidos-badge');
      badgeEl.textContent = String(d.total);
      badgeEl.hidden = d.total === 0;
      const actual = d.pedidos.map((p) => String(p.id)).sort().join(',');
      let anterior = null;
      try { anterior = sessionStorage.getItem('pv_pedidos_vistos'); } catch (e) { /* ignorar */ }
      if (anterior !== null && anterior !== actual) {
        const vistos = new Set(anterior.split(',').filter(Boolean));
        const nuevos = d.pedidos.filter((p) => !vistos.has(String(p.id)));
        if (nuevos.length) toast(`🔔 ${nuevos.length} pedido(s) nuevo(s) · ${nuevos[0].numero}`);
      }
      try { sessionStorage.setItem('pv_pedidos_vistos', actual); } catch (e) { /* ignorar */ }
    } catch (e) { /* la notificación nunca interrumpe al administrador */ }
  }

  // -------------------------------------------------- ventas y clientes frecuentes
  async function loadVentas() {
    try {
      const d = await api('/api/admin/ventas');
      $('v-hoy').textContent = money(d.ventas_hoy);
      $('v-semana').textContent = money(d.ventas_semana);
      $('v-mes').textContent = money(d.ventas_mes);
      $('v-total').textContent = money(d.total_ventas);
      $('v-promedio').textContent = money(d.promedio_compra);
      $('v-pedidos').textContent = d.total_pedidos;
      const max = Math.max(1, ...d.serie_ventas.map((x) => x.total));
      $('chart-ventas').innerHTML = d.serie_ventas.map((x) => {
        const label = x.fecha.slice(5).split('-').reverse().join('/');
        const h = Math.max(3, Math.round((x.total / max) * 160));
        return `<div class="chart-col"><div class="chart-bar" style="height:${h}px"><span>${x.total}</span></div><small>${label}</small></div>`;
      }).join('');
      $('cobros-estado').innerHTML = `
        <div class="cobro-row"><span>⏳ Pendientes de pago</span><strong>${d.pendientes_pago} · ${money(d.monto_pendiente)}</strong></div>
        <div class="cobro-row"><span>✅ Pagados (ventas definitivas)</span><strong>${d.pagadas} · ${money(d.monto_pagado)}</strong></div>
        <div class="cobro-row"><span>🚚 Pagados y entregados</span><strong>${d.entregadas} · ${money(d.monto_entregado)}</strong></div>`;
      $('ventas-productos').innerHTML = d.productos_top.length ? d.productos_top.map((p) =>
        `<tr><td>${esc(p.nombre)}</td><td><span class="tag">${p.cantidad}</span></td><td>${money(p.total)}</td></tr>`).join('')
        : '<tr><td colspan="3" class="muted">Aún no hay ventas pagadas.</td></tr>';
      $('ventas-categorias').innerHTML = d.ingresos_categoria.length ? d.ingresos_categoria.map((c) =>
        `<tr><td>${esc(c.categoria)}</td><td>${money(c.total)}</td></tr>`).join('')
        : '<tr><td colspan="2" class="muted">Aún no hay ingresos por categoría.</td></tr>';
      $('ventas-historial').innerHTML = d.historial.length ? d.historial.map((v) =>
        `<tr><td><strong>${esc(v.numero)}</strong></td><td>${esc(v.cliente)}</td><td>${esc(String(v.fecha).replace('T', ' ').slice(0, 16))}</td><td>${badge(v.estado, ESTADOS)}</td><td>${money(v.total)}</td></tr>`).join('')
        : '<tr><td colspan="5" class="muted">Aún no hay ventas registradas.</td></tr>';
    } catch (e) { toast(e.message, true); }
  }

  async function loadFrecuentes() {
    try {
      const d = await api('/api/admin/clientes/frecuentes');
      $('frecuentes-body').innerHTML = d.clientes.length ? d.clientes.map((c) => `
        <tr><td><strong>${esc(c.nombre)}</strong></td><td>${esc(c.telefono)}</td>
        <td><span class="tag">${c.pedidos}</span></td><td>${money(c.total)}</td><td>${money(c.promedio)}</td>
        <td>${esc(String(c.ultima).replace('T', ' ').slice(0, 10))}</td>
        <td><button class="mini-btn" data-profile="${c.cliente_id}">Ver perfil</button></td></tr>`).join('')
        : '<tr><td colspan="7" class="muted">Aún no hay clientes con compras pagadas.</td></tr>';
      $('frecuentes-body').querySelectorAll('[data-profile]').forEach((b) =>
        b.addEventListener('click', () => showPerfil(Number(b.dataset.profile))));
    } catch (e) { toast(e.message, true); }
  }

  async function showPerfil(id) {
    try {
      const d = await api(`/api/admin/clientes/${id}/perfil`);
      $('profile-title').textContent = 'Perfil de ' + d.cliente.nombre;
      let html = `<div class="profile-head"><span class="avatar">${esc(d.cliente.nombre[0] || '?')}</span>
        <div><strong>${esc(d.cliente.nombre)}</strong><small>Tel. ${esc(d.cliente.telefono)}</small></div></div>
        <div class="profile-stats">
          <div><small>Total de visitas</small><strong>${d.total_visitas}</strong></div>
          <div><small>Total de pedidos</small><strong>${d.total_pedidos}</strong></div>
          <div><small>Total gastado</small><strong>${money(d.total_gastado)}</strong></div>
          <div><small>Promedio de compra</small><strong>${money(d.promedio_compra)}</strong></div>
          <div><small>Última visita</small><strong>${esc(d.ultima_visita)}</strong></div>
          <div><small>Última compra</small><strong>${esc(String(d.ultima_compra).slice(0, 10))}</strong></div>
        </div>`;
      if (d.favoritos.length) {
        html += '<h4>Productos que compra con mayor frecuencia</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>Producto</th><th>Unidades</th></tr></thead><tbody>'
          + d.favoritos.map((f) => `<tr><td>${esc(f.nombre)}</td><td><span class="tag">${f.cantidad}</span></td></tr>`).join('')
          + '</tbody></table></div>';
      }
      html += '<h4>Historial completo de pedidos</h4><div class="table-wrap"><table class="data-table"><thead><tr><th>N.º pedido</th><th>Fecha</th><th>Estado</th><th>Total</th><th></th></tr></thead><tbody>';
      html += d.pedidos.length ? d.pedidos.map((p) => `<tr><td><strong>${esc(p.numero)}</strong></td><td>${esc(String(p.creado).replace('T', ' ').slice(0, 16))}</td><td>${badge(p.estado, ESTADOS)}</td><td>${money(p.total)}</td><td><button class="mini-btn" data-ver-pedido="${p.id}">Ver detalle</button></td></tr>`).join('')
        : '<tr><td colspan="5" class="muted">Este cliente aún no tiene pedidos.</td></tr>';
      html += '</tbody></table></div>';
      $('profile-body').innerHTML = html;
      $('profile-backdrop').classList.remove('hidden');
    } catch (e) { toast(e.message, true); }
  }

  async function loadClientes() {
    try {
      const d = await api('/api/admin/clientes?q=' + encodeURIComponent($('cliente-q').value));
      $('clientes-body').innerHTML = d.clientes.length ? d.clientes.map((c) => `
        <tr><td><strong>${esc(c.nombre)}</strong></td><td>${esc(c.telefono)}</td>
        <td><span class="tag">${c.total}</span></td><td>${esc(c.primera)}</td><td>${esc(c.ultima)}</td>
        <td><div class="row-actions"><button class="mini-btn" data-profile="${c.id}">Perfil</button><button class="mini-btn" data-history="${c.id}">Historial</button><button class="mini-btn danger" data-clear-history="${c.id}" title="Borrar todas las visitas de este cliente">🗑 Borrar historial</button><button class="mini-btn danger" data-delete-customer="${c.id}" title="Eliminar el cliente y todo su historial">🗑 Eliminar cliente</button></div></td></tr>`).join('')
        : '<tr><td colspan="6" class="muted">No hay clientes para ese filtro.</td></tr>';
      $('clientes-body').querySelectorAll('[data-profile]').forEach((b) =>
        b.addEventListener('click', () => showPerfil(Number(b.dataset.profile))));
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
      $('clientes-body').querySelectorAll('[data-delete-customer]').forEach((b) =>
        b.addEventListener('click', () => {
          const fila = b.closest('tr');
          borrarCliente(
            Number(b.dataset.deleteCustomer),
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
        `<div class="row-actions history-actions"><button class="btn btn-outline btn-sm btn-danger history-clear" data-borrar-historial="${d.cliente.id}" type="button">🗑 Borrar historial de ${esc(d.cliente.nombre)}</button><button class="btn btn-outline btn-sm btn-danger history-clear" data-borrar-cliente="${d.cliente.id}" data-visitas="${d.visitas.length}" type="button">🗑 Eliminar cliente</button></div>`;
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

  async function borrarCliente(id, nombre = 'este cliente', total = 0) {
    const aviso = `¿Eliminar a ${nombre}? Se borrarán también sus ${total} visita(s) y no se puede deshacer.`;
    if (!confirm(aviso)) return;
    try {
      const d = await api(`/api/admin/clientes/${id}`, {method: 'DELETE'});
      toast(`Cliente eliminado junto con ${d.visitas} visita(s)`);
      if (!$('history-backdrop').classList.contains('hidden')) $('history-backdrop').classList.add('hidden');
      loadClientes();
      loadResumen();
    } catch (e) { toast(e.message, true); }
  }

  $('history-body').addEventListener('click', (e) => {
    const historial = e.target.closest('[data-borrar-historial]');
    if (historial) borrarHistorial(Number(historial.dataset.borrarHistorial), $('history-title').textContent.replace('Historial · ', ''));
    const cliente = e.target.closest('[data-borrar-cliente]');
    if (cliente) borrarCliente(Number(cliente.dataset.borrarCliente), $('history-title').textContent.replace('Historial · ', ''), Number(cliente.dataset.visitas || 0));
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
  checkPedidosNuevos();
  setInterval(checkPedidosNuevos, 20000);
})();
