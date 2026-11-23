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
            <button class="btn-detail" data-id="${p.id}">Ver detalle</button>
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
        <button class="btn btn-primary" id="share-product">↗ Compartir este producto</button>
      </div>`;
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
