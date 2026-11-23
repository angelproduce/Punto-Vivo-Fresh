/* PUNTO VIVO FRESH · instalación PWA del menú y del panel compartida por ambas vistas */
(() => {
  'use strict';
  const botones = Array.from(document.querySelectorAll('[data-install-app]'));
  if (!botones.length) return;

  const avisar = (texto) => {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = texto;
    toast.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { toast.hidden = true; }, 3600);
  };

  const yaInstalada = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  if (yaInstalada) {
    botones.forEach((boton) => { boton.hidden = true; });
    return;
  }

  // iPadOS 13+ se identifica como macOS, pero tiene pantalla táctil.
  const esAppleMovil = () => /iPad|iPhone|iPod/.test(navigator.userAgent)
    || ((navigator.platform === 'MacIntel' || /Macintosh/.test(navigator.userAgent)) && navigator.maxTouchPoints > 1);

  let cerrarGuia = null;
  const abrirGuiaApple = () => {
    let capa = document.getElementById('pv-install-guide');
    if (!capa) {
      capa = document.createElement('div');
      capa.id = 'pv-install-guide';
      capa.className = 'pv-guide-backdrop';
      capa.innerHTML = `
        <div class="pv-guide-card" role="dialog" aria-modal="true" aria-labelledby="pv-guide-title">
          <div class="pv-guide-head">
            <div><span class="eyebrow">IPHONE E IPAD</span><h3 id="pv-guide-title">Instalar en tu iPhone</h3></div>
            <button class="pv-guide-close" type="button" aria-label="Cerrar guía">✕</button>
          </div>
          <div class="pv-guide-body">
            <p class="pv-guide-lead">En Apple un botón web no puede instalar la app directamente. Al pulsar «Instalar» ya tienes aquí los pasos; solo tardas un momento:</p>
            <ol class="pv-guide-steps">
              <li><span class="pv-guide-num">1</span><span>Pulsa el botón <strong>Compartir</strong> <span class="pv-guide-share" aria-hidden="true">⬆️</span> de Safari (abajo o arriba, según tu modelo).</span></li>
              <li><span class="pv-guide-num">2</span><span>Desliza hacia abajo y elige <strong>«Añadir a pantalla de inicio»</strong>.</span></li>
              <li><span class="pv-guide-num">3</span><span>Pulsa <strong>«Añadir»</strong> en la esquina superior derecha.</span></li>
            </ol>
            <p class="pv-guide-note">Abre esta página con <strong>Safari</strong>. Si la estás viendo en Chrome, Facebook u otra app, copia el enlace y ábrelo en Safari.</p>
          </div>
          <div class="pv-guide-foot"><button class="btn btn-primary" type="button" id="pv-guide-ok">Entendido</button></div>
        </div>`;
      document.body.appendChild(capa);
      const cerrar = () => {
        capa.remove();
        cerrarGuia = null;
        document.removeEventListener('keydown', alTeclado);
        if (botonAnterior && typeof botonAnterior.focus === 'function') botonAnterior.focus();
      };
      cerrarGuia = cerrar;
      const alTeclado = (event) => { if (event.key === 'Escape') cerrar(); };
      const botonAnterior = document.activeElement;
      capa.querySelector('.pv-guide-close').addEventListener('click', cerrar);
      capa.querySelector('#pv-guide-ok').addEventListener('click', cerrar);
      capa.addEventListener('click', (event) => { if (event.target === capa) cerrar(); });
      document.addEventListener('keydown', alTeclado);
      capa.querySelector('#pv-guide-ok').focus();
    }
  };

  let promptInstalar = null;
  botones.forEach((boton) => { boton.hidden = false; });

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    promptInstalar = event;
    botones.forEach((boton) => { boton.hidden = false; });
  });

  botones.forEach((boton) => {
    boton.addEventListener('click', async () => {
      // Safari iOS nunca dispara beforeinstallprompt: mostramos la guía al instante.
      if (esAppleMovil()) { abrirGuiaApple(); return; }
      if (promptInstalar) {
        const evento = promptInstalar;
        promptInstalar = null;
        evento.prompt();
        const {outcome} = await evento.userChoice;
        if (outcome === 'accepted') avisar('App instalada en tu dispositivo');
        return;
      }
      avisar('En el menú del navegador elige «Añadir a pantalla de inicio» o «Instalar app».');
    });
  });

  window.addEventListener('appinstalled', () => {
    botones.forEach((boton) => { boton.hidden = true; });
    if (cerrarGuia) cerrarGuia();
    avisar('App instalada en tu dispositivo');
  });
})();
