/* PUNTO VIVO FRESH · formulario de registro y menú público */
(() => {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function newToken() {
    const value = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : 'pv' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    sessionStorage.setItem('pv_request_token', value);
    return value;
  }

  // -------------------------------------------------- formulario de bienvenida
  const form = document.getElementById('visita-form');
  if (form) {
    const token = sessionStorage.getItem('pv_request_token') || newToken();
    const msg = document.getElementById('form-msg');
    const btn = document.getElementById('submit-btn');
    const dialog = document.getElementById('privacy-dialog');
    const consent = document.getElementById('consent');

    const showError = (field, text) => { document.getElementById('err-' + field).textContent = text || ''; };
    document.getElementById('open-privacy').addEventListener('click', () => dialog.showModal());
    document.getElementById('close-privacy').addEventListener('click', () => dialog.close());
    document.getElementById('accept-privacy').addEventListener('click', () => {
      consent.checked = true;
      dialog.close();
    });
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });

    ['full_name', 'phone'].forEach((id) => {
      document.getElementById(id).addEventListener('input', () => showError(id, ''));
    });
    consent.addEventListener('change', () => showError('consent', ''));

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const nombre = document.getElementById('full_name').value.trim();
      const telefono = document.getElementById('phone').value.trim();
      const digits = telefono.replace(/\D/g, '');
      let valid = true;

      if (nombre.length < 3) { showError('full_name', 'Escribe tu nombre completo.'); valid = false; }
      if (digits.length !== 10) { showError('phone', 'El teléfono debe tener 10 dígitos.'); valid = false; }
      if (!consent.checked) { showError('consent', 'Debes aceptar el aviso de privacidad.'); valid = false; }
      if (!valid) { msg.hidden = true; return; }

      btn.disabled = true;
      btn.classList.add('loading');
      msg.hidden = true;

      try {
        const res = await fetch('/api/visita', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            full_name: nombre,
            phone: digits,
            consent: consent.checked,
            request_token: token,
            website: document.getElementById('website').value
          })
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.detail || 'No se pudo guardar la visita.');

        // Solo se redirige si el servidor confirmó el guardado.
        sessionStorage.removeItem('pv_request_token');
        sessionStorage.setItem('pv_visit_ok', String(data.visit_number));
        location.href = data.menu_url;
      } catch (err) {
        msg.textContent = err.message + ' Revisa los datos e inténtalo de nuevo.';
        msg.hidden = false;
        btn.disabled = false;
        btn.classList.remove('loading');
      }
    });
  }

  // -------------------------------------------------- menú público
  const grid = document.getElementById('product-grid');
  if (!grid) return;

  const state = { productos: [], categoria: 'all', q: '' };
  const chips = document.getElementById('cat-chips');
  const count = document.getElementById('result-count');
  const empty = document.getElementById('empty-state');
  const dialog = document.getElementById('product-dialog');
  const detail = document.getElementById('product-detail');
  const fallbackIcon = {'Hamburguesas':'🍔','Sándwiches':'🥪','Papas fritas y acompañantes':'🍟','Jugos naturales':'🧃','Batidas':'🥤','Ensaladas':'🥗'};

  const toast = (text) => {
    const el = document.getElementById('toast');
    el.textContent = text;
    el.hidden = false;
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.hidden = true; }, 2800);
  };

  function imageHtml(item, cls = '') {
    if (!item.image) return `<div class="${cls} fallback">${fallbackIcon[item.categoria] || '🍽️'}</div>`;
    const icon = fallbackIcon[item.categoria] || '🍽️';
    return `<div class="${cls}"><img src="${esc(item.image)}" alt="${esc(item.name)}" loading="lazy" decoding="async"
      onerror="var p=this.parentElement;this.remove();p.classList.add('fallback');p.textContent='${icon}'"></div>`;
  }

  function render() {
    const q = state.q.trim().toLowerCase();
    const items = state.productos.filter((p) =>
      (state.categoria === 'all' || String(p.category_id) === state.categoria) &&
      (!q || (p.name + ' ' + p.description + ' ' + p.categoria).toLowerCase().includes(q))
    );
    count.textContent = items.length
      ? `Mostrando ${items.length} producto${items.length === 1 ? '' : 's'}`
      : 'Sin resultados';
    empty.hidden = items.length > 0;
    grid.innerHTML = items.map((p) => `
      <article class="product-card">
        ${imageHtml(p, 'product-image')}
        ${p.is_demo ? '<span class="demo-tag" style="position:absolute">DEMO</span>' : ''}
        <div class="product-body">
          <span class="product-cat">${esc(p.categoria)}</span>
          <h3>${esc(p.name)}</h3>
          <p class="product-desc">${esc(p.description)}</p>
          <div class="product-bottom">
            <span class="price ${p.price_label === 'Precio por definir' ? 'small' : ''}">${esc(p.price_label)}</span>
            <div class="product-actions">
              <button class="btn-detail" data-id="${p.id}">Ver detalle</button>
              <button class="btn-cart-add" data-add="${p.id}" ${Number(p.price) > 0 ? '' : 'disabled title="Precio por definir"'}>🛒 Agregar</button>
            </div>
          </div>
        </div>
      </article>`).join('');
    grid.querySelectorAll('.btn-detail').forEach((b) => b.addEventListener('click', () => openProduct(Number(b.dataset.id))));
  }

  function openProduct(id) {
    const p = state.productos.find((x) => x.id === id);
    if (!p) return;
    detail.innerHTML = `
      ${imageHtml(p, 'product-detail-img')}
      <div class="product-detail-content">
        ${p.is_demo ? '<span class="tag demo">DATO DE DEMOSTRACIÓN</span>' : ''}
        <span class="product-cat">${esc(p.categoria)}</span>
        <h2>${esc(p.name)}</h2>
        <span class="price ${p.price_label === 'Precio por definir' ? 'small' : ''}">${esc(p.price_label)}</span>
        <p>${esc(p.description)}</p>
        <div class="detail-order">
          <label>Cantidad<input type="number" id="detail-qty" min="1" max="99" value="1"></label>
          <label>Observación (opcional)<input type="text" id="detail-notes" maxlength="200" placeholder="Ej.: Sin cebolla"></label>
          <button class="btn btn-primary" id="add-from-detail" ${Number(p.price) > 0 ? '' : 'disabled'}>🛒 Agregar al carrito</button>
        </div>
        <button class="btn btn-outline" id="share-product">↗ Compartir este producto</button>
      </div>`;
    detail.querySelector('#add-from-detail')?.addEventListener('click', () => {
      const qty = Math.max(1, Math.min(99, Number(detail.querySelector('#detail-qty').value) || 1));
      document.dispatchEvent(new CustomEvent('pv:add', {detail: {
        id: p.id, quantity: qty, notes: detail.querySelector('#detail-notes').value,
      }}));
      dialog.close();
    });
    detail.querySelector('#share-product').addEventListener('click', async () => {
      const url = location.href.split('#')[0] + '#producto-' + p.id;
      try {
        if (navigator.share) await navigator.share({title: p.name, text: p.description, url});
        else { await navigator.clipboard.writeText(url); toast('Enlace del producto copiado'); }
      } catch (e) { /* usuario canceló */ }
    });
    dialog.showModal();
  }

  document.getElementById('close-product').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  document.getElementById('search').addEventListener('input', (e) => { state.q = e.target.value; render(); });

  document.getElementById('share-menu').addEventListener('click', async () => {
    const url = location.origin + '/bienvenida';
    try {
      if (navigator.share) await navigator.share({title: 'PUNTO VIVO FRESH', text: 'Menú digital de Punto Vivo Fresh', url});
      else { await navigator.clipboard.writeText(url); toast('Enlace copiado: ' + url); }
    } catch (e) { /* cancelado */ }
  });

  // -------------------------------------------------- vista móvil / escritorio
  const modeBtn = document.getElementById('mode-toggle');
  if (modeBtn) {
    const MODE_KEY = 'pv_menu_mode';
    const esMovil = (modo) => modo === 'mobile' || (modo === 'auto' && window.innerWidth <= 700);
    const aplicarModo = (modo) => {
      document.body.classList.toggle('pv-mobile', modo === 'mobile');
      document.body.classList.toggle('pv-desktop', modo === 'desktop');
      const movil = esMovil(modo);
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
      if (!document.body.classList.contains('pv-mobile') && !document.body.classList.contains('pv-desktop')) aplicarModo('auto');
    });
  }

  fetch('/api/visita/actual').then((r) => r.ok ? r.json() : null).then((data) => {
    if (data) document.getElementById('visit-chip').textContent = `🎟️ Visita #${data.visit_number} registrada`;
  }).catch(() => {});

  fetch('/api/menu').then(async (res) => {
    if (!res.ok) { location.href = '/bienvenida?motivo=requerido'; return; }
    return res.json();
  }).then((data) => {
    if (!data) return;
    state.productos = data.productos;
    chips.innerHTML = '<button class="chip is-active" data-cat="all">Todo</button>' +
      data.categorias.map((c) => `<button class="chip" data-cat="${c.id}">${esc(c.name)}</button>`).join('');
    chips.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => {
      state.categoria = chip.dataset.cat;
      chips.querySelectorAll('.chip').forEach((c) => c.classList.toggle('is-active', c === chip));
      render();
    }));
    render();
    if (location.hash.startsWith('#producto-')) {
      const id = Number(location.hash.replace('#producto-', ''));
      if (state.productos.some((p) => p.id === id)) openProduct(id);
    }
  }).catch(() => {
    count.textContent = 'No se pudo cargar el menú.';
  });
})();

/* ------------------------------------------------ carrito y confirmación */
(() => {
  'use strict';
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (v) => 'RD$ ' + Number(v || 0).toLocaleString('es-DO', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const CART_KEY = 'pv_carrito';
  const NOTES_KEY = 'pv_carrito_notas';
  const TOKEN_KEY = 'pv_pedido_token';
  const cartDialog = document.getElementById('cart-dialog');
  if (!cartDialog) return;

  const checkoutDialog = document.getElementById('checkout-dialog');
  const successDialog = document.getElementById('success-dialog');
  const cartItems = document.getElementById('cart-items');
  const cartEmpty = document.getElementById('cart-empty');
  const subtotalEl = document.getElementById('cart-subtotal');
  const checkoutTotal = document.getElementById('checkout-total');
  const checkoutResumen = document.getElementById('checkout-resumen');
  const confirmBtn = document.getElementById('confirm-order');
  const toastEl = document.getElementById('toast');

  const toast = (texto, error = false) => {
    toastEl.textContent = texto;
    toastEl.style.background = error ? '#a32828' : '';
    toastEl.hidden = false;
    clearTimeout(toastEl._timer);
    toastEl._timer = setTimeout(() => { toastEl.hidden = true; }, 3500);
  };

  const leer = (clave, defecto) => {
    try { return JSON.parse(localStorage.getItem(clave)) ?? defecto; } catch (e) { return defecto; }
  };
  const guardar = (clave, valor) => {
    try { localStorage.setItem(clave, JSON.stringify(valor)); } catch (e) { /* almacenamiento bloqueado */ }
  };

  let items = leer(CART_KEY, []);
  let notasGenerales = leer(NOTES_KEY, '');
  let menuCache = null;
  if (!Array.isArray(items)) items = [];
  items = items.filter((i) => i && Number(i.id) > 0 && Number(i.price) > 0)
    .map((i) => ({
      id: Number(i.id), name: String(i.name || 'Producto'), image: String(i.image || ''),
      price: Number(i.price), qty: Math.max(1, Math.min(99, Number(i.qty) || 1)),
      notes: String(i.notes || '').slice(0, 200),
    }));
  notasGenerales = String(notasGenerales || '').slice(0, 500);

  const subtotal = () => items.reduce((sum, i) => sum + i.price * i.qty, 0);
  const saveCart = () => guardar(CART_KEY, items);
  const saveNotes = () => guardar(NOTES_KEY, notasGenerales);

  function renderBadges() {
    const unidades = items.reduce((sum, i) => sum + i.qty, 0);
    [document.getElementById('cart-count'), document.getElementById('cart-fab-count')].forEach((el) => {
      if (!el) return;
      el.textContent = String(unidades);
      el.hidden = unidades === 0;
    });
    document.getElementById('cart-fab-total').textContent = money(subtotal());
    document.getElementById('go-checkout').disabled = items.length === 0;
  }

  function imageHtml(item) {
    if (!item.image) return '<div class="cart-thumb fallback">🍽️</div>';
    return `<div class="cart-thumb"><img src="${esc(item.image)}" alt="" loading="lazy"></div>`;
  }

  function renderCart() {
    cartItems.innerHTML = items.map((item, index) => `
      <div class="cart-line">
        ${imageHtml(item)}
        <div class="cart-line-main">
          <strong>${esc(item.name)}</strong>
          <small>${money(item.price)} c/u</small>
          <textarea data-cart-notes="${index}" rows="2" maxlength="200" placeholder="Observación: sin cebolla, extra queso…">${esc(item.notes)}</textarea>
        </div>
        <div class="cart-line-side">
          <div class="qty-control">
            <button type="button" data-cart-dec="${index}" aria-label="Quitar una unidad">−</button>
            <span>${item.qty}</span>
            <button type="button" data-cart-inc="${index}" aria-label="Agregar una unidad">+</button>
          </div>
          <strong>${money(item.price * item.qty)}</strong>
          <button type="button" class="cart-remove" data-cart-del="${index}" title="Eliminar del carrito">🗑 Eliminar</button>
        </div>
      </div>`).join('');
    cartEmpty.hidden = items.length > 0;
    subtotalEl.textContent = money(subtotal());
    renderBadges();
  }

  async function ensureMenu(force = false) {
    if (menuCache && !force) return menuCache;
    const res = await fetch('/api/menu');
    if (!res.ok) throw new Error('No se pudo validar el menú. Vuelve a intentarlo.');
    const data = await res.json();
    menuCache = data.productos || [];
    return menuCache;
  }

  async function agregar(id, cantidad = 1, nota = '') {
    const boton = document.querySelector(`[data-add="${id}"]`);
    if (boton) boton.disabled = true;
    try {
      const productos = await ensureMenu();
      const prod = productos.find((p) => Number(p.id) === Number(id));
      if (!prod || !prod.available || Number(prod.price) <= 0) {
        throw new Error('Ese producto ya no está disponible.');
      }
      const qty = Math.max(1, Math.min(99, Number(cantidad) || 1));
      const actual = items.find((i) => i.id === Number(id));
      if (actual) {
        actual.qty = Math.min(99, actual.qty + qty);
        if (nota) actual.notes = (actual.notes ? actual.notes + '; ' : '') + String(nota).trim().slice(0, 200);
        actual.price = Number(prod.price);
      } else {
        items.push({
          id: Number(id), name: prod.name, image: prod.image || '', price: Number(prod.price),
          qty, notes: String(nota || '').trim().slice(0, 200),
        });
      }
      saveCart();
      renderCart();
      toast('Producto agregado al carrito');
    } catch (err) {
      toast(err.message, true);
    } finally {
      if (boton) boton.disabled = false;
    }
  }

  document.addEventListener('click', (event) => {
    const boton = event.target.closest('[data-add]');
    if (boton) { event.preventDefault(); agregar(Number(boton.dataset.add), 1, ''); }
  });
  document.addEventListener('pv:add', (event) => {
    const data = event.detail || {};
    agregar(Number(data.id), Number(data.quantity) || 1, data.notes || '');
  });

  const abrirCarrito = () => { renderCart(); cartDialog.showModal(); };
  document.getElementById('cart-button').addEventListener('click', abrirCarrito);
  document.getElementById('cart-fab').addEventListener('click', abrirCarrito);
  ['close-cart', 'close-cart-2'].forEach((id) =>
    document.getElementById(id).addEventListener('click', () => cartDialog.close()));
  cartDialog.addEventListener('click', (e) => { if (e.target === cartDialog) cartDialog.close(); });

  cartDialog.addEventListener('click', (event) => {
    const boton = event.target.closest('button');
    if (!boton) return;
    const index = Number(boton.dataset.cartInc ?? boton.dataset.cartDec ?? boton.dataset.cartDel ?? -1);
    if (boton.dataset.cartInc !== undefined) {
      if (items[index]) items[index].qty = Math.min(99, items[index].qty + 1);
    } else if (boton.dataset.cartDec !== undefined) {
      if (items[index]) items[index].qty -= 1;
      if (items[index] && items[index].qty < 1) items.splice(index, 1);
    } else if (boton.dataset.cartDel !== undefined) {
      items.splice(index, 1);
    } else {
      return;
    }
    saveCart();
    renderCart();
  });
  cartDialog.addEventListener('input', (event) => {
    const area = event.target.closest('[data-cart-notes]');
    if (!area) return;
    const index = Number(area.dataset.cartNotes);
    if (items[index]) { items[index].notes = area.value.slice(0, 200); saveCart(); }
  });

  async function jsonGet(path) {
    const res = await fetch(path);
    let data = {};
    try { data = await res.json(); } catch (e) { /* respuesta vacía */ }
    if (!res.ok) throw new Error(data.detail || 'No se pudo completar la solicitud.');
    return data;
  }

  async function validarCarrito() {
    const productos = await ensureMenu(true);
    const eliminados = [];
    const actualizados = [];
    items = items.filter((item) => {
      const prod = productos.find((p) => Number(p.id) === item.id);
      if (!prod || !prod.available || Number(prod.price) <= 0) { eliminados.push(item.name); return false; }
      if (Number(prod.price) !== item.price || prod.name !== item.name) {
        actualizados.push(item.name);
        item.price = Number(prod.price);
        item.name = prod.name;
        item.image = prod.image || '';
      }
      return true;
    });
    saveCart();
    renderCart();
    if (eliminados.length) toast('Ya no disponibles: ' + eliminados.join(', '), true);
    if (actualizados.length) toast('Precios actualizados: ' + actualizados.join(', '));
    if (!items.length) throw new Error('Agrega productos disponibles al carrito.');
  }

  document.getElementById('go-checkout').addEventListener('click', async () => {
    try {
      await validarCarrito();
      const cliente = await jsonGet('/api/cliente/actual');
      document.getElementById('checkout-nombre').textContent = cliente.nombre;
      document.getElementById('checkout-telefono').textContent = 'Tel. ' + cliente.telefono;
      document.getElementById('checkout-notes').value = notasGenerales;
      cartDialog.close();
      renderCheckout();
      checkoutDialog.showModal();
    } catch (err) { toast(err.message, true); }
  });

  function renderCheckout() {
    checkoutResumen.innerHTML = items.map((i) => `
      <div class="checkout-line">
        <span><strong>${esc(i.name)}</strong> × ${i.qty}${i.notes ? `<small>${esc(i.notes)}</small>` : ''}</span>
        <strong>${money(i.price * i.qty)}</strong>
      </div>`).join('');
    checkoutTotal.textContent = money(subtotal());
  }

  const toggleDireccion = () => {
    const entrega = document.querySelector('input[name="checkout-entrega"]:checked')?.value || 'retiro';
    document.getElementById('checkout-direccion').hidden = entrega !== 'delivery';
  };
  document.querySelectorAll('input[name="checkout-entrega"]').forEach((radio) =>
    radio.addEventListener('change', toggleDireccion));

  document.getElementById('close-checkout').addEventListener('click', () => checkoutDialog.close());
  checkoutDialog.addEventListener('click', (e) => { if (e.target === checkoutDialog) checkoutDialog.close(); });
  document.getElementById('back-to-cart').addEventListener('click', () => {
    checkoutDialog.close();
    renderCart();
    cartDialog.showModal();
  });
  document.getElementById('checkout-notes').addEventListener('input', (e) => {
    notasGenerales = e.target.value.slice(0, 500);
    saveNotes();
  });

  function tokenPedido() {
    let token = null;
    try { token = sessionStorage.getItem(TOKEN_KEY); } catch (e) { /* ignorar */ }
    if (!token) {
      token = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : 'pv' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      try { sessionStorage.setItem(TOKEN_KEY, token); } catch (e) { /* ignorar */ }
    }
    return token;
  }

  confirmBtn.addEventListener('click', async () => {
    if (confirmBtn.disabled) return;
    const entrega = document.querySelector('input[name="checkout-entrega"]:checked')?.value || 'retiro';
    const direccion = entrega === 'delivery' ? {
      address_line1: document.getElementById('checkout-dir1').value.trim(),
      address_line2: document.getElementById('checkout-dir2').value.trim(),
      city: document.getElementById('checkout-ciudad').value.trim(),
      province: document.getElementById('checkout-provincia').value.trim(),
    } : null;
    if (entrega === 'delivery' && direccion.address_line1.length < 4) {
      toast('Escribe la dirección de entrega.', true);
      document.getElementById('checkout-dir1').focus();
      return;
    }
    const body = {
      items: items.map((i) => ({product_id: i.id, quantity: i.qty, notes: i.notes})),
      request_token: tokenPedido(),
      delivery_method: entrega,
      address: direccion,
      notes: notasGenerales.trim(),
      payment_method: document.getElementById('checkout-pago').value,
    };
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Enviando…';
    try {
      const res = await fetch('/api/pedido', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      });
      let data = {};
      try { data = await res.json(); } catch (e) { /* respuesta vacía */ }
      if (!res.ok) throw new Error(data.detail || 'No se pudo confirmar el pedido.');
      try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignorar */ }
      items = [];
      notasGenerales = '';
      saveCart();
      saveNotes();
      renderCart();
      checkoutDialog.close();
      document.getElementById('success-numero').textContent = data.numero || '—';
      document.getElementById('success-mensaje').textContent = data.mensaje || 'Te esperamos.';
      successDialog.showModal();
    } catch (err) {
      toast(err.message, true);
    } finally {
      confirmBtn.disabled = false;
      confirmBtn.textContent = 'Confirmar pedido';
    }
  });

  document.getElementById('success-close').addEventListener('click', () => successDialog.close());
  successDialog.addEventListener('click', (e) => { if (e.target === successDialog) successDialog.close(); });
  toggleDireccion();
  renderCart();
})();
