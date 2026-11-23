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

  let promptInstalar = null;
  botones.forEach((boton) => { boton.hidden = false; });

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    promptInstalar = event;
    botones.forEach((boton) => { boton.hidden = false; });
  });

  botones.forEach((boton) => {
    boton.addEventListener('click', async () => {
      if (promptInstalar) {
        const evento = promptInstalar;
        promptInstalar = null;
        evento.prompt();
        const {outcome} = await evento.userChoice;
        if (outcome === 'accepted') avisar('App instalada en tu dispositivo');
        return;
      }
      const iphone = /iPad|iPhone|iPod/.test(navigator.userAgent);
      avisar(iphone
        ? 'En iPhone: pulsa Compartir y elige «Añadir a pantalla de inicio».'
        : 'En el menú del navegador elige «Añadir a pantalla de inicio» o «Instalar app».');
    });
  });

  window.addEventListener('appinstalled', () => {
    botones.forEach((boton) => { boton.hidden = true; });
    avisar('App instalada en tu dispositivo');
  });
})();
