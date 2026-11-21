/* ANGEL PRODUCE IA · interfaz local */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const TITLES = {
    create: ["Crear", "Tu voz, tus temas, tus shorts. Sin salir del PC."],
    review: ["Revisar", "Mira cada short y decide qué se publica."],
    voice: ["Mi voz", "Una grabación de 15 segundos y listo."],
    settings: ["Ajustes", "Todo lo que hay debajo del capó, sin tecnicismos."],
    gta: ["GTA Media Center", "Noticias, imágenes y videos de GTA 6 y Rockstar."],
    ytclips: ["YouTube Clips", "Genera clips cortos con subtítulos desde YouTube o desde tu vídeo."],
  };

  const STATUS_LABEL = { pending: "Pendiente", approved: "Aprobado", published: "Publicado" };
  const EMPTY_TEXT = {
    pending: "Genera tu primer short y aparecerá aquí listo para revisar.",
    approved: "Nada aprobado todavía. Ve a Pendientes y aprueba lo que te guste.",
    published: "Aún no has publicado nada. Cuando lo hagas, quedará archivado aquí.",
  };

  const state = {
    data: null,
    tab: "create",
    queueTab: "pending",
    counts: { pending: 1, approved: 2, published: 3 },
    count: 1,
    seconds: 90,
    touchedDuration: false,
    job: null,
    dismissed: 0,
    uploading: false,
    timer: null,
    fast: false,
    audioPlaying: false,
    modalItem: null,
    autoMode: (localStorage.getItem("autoMode") === "1"),
    resourceUploading: false,
    ytVideo: (() => {
      try {
        const saved = localStorage.getItem("selectedYtVideo") || "";
        return typeof saved === "string" ? saved : "";
      } catch (_) {
        return "";
      }
    })(),
    resourceSelection: (() => {
      try {
        const saved = JSON.parse(localStorage.getItem("selectedResources") || "[]");
        return Array.isArray(saved) ? saved.filter((x) => typeof x === "string") : [];
      } catch (_) {
        return [];
      }
    })(),
    sources: (() => {
      try {
        const saved = JSON.parse(localStorage.getItem("sourceUrls") || "[]");
        return Array.isArray(saved) ? saved.filter((x) => typeof x === "string").slice(0, 5) : [];
      } catch (_) {
        return [];
      }
    })(),
  };

  /* ------------------------------------------------------------- utils */

  function esc(text) {
    return String(text ?? "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

  function fmtTime(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function fmtMinutes(seconds) {
    const mins = Math.max(1, Math.round((seconds || 0) / 60));
    return `≈ ${mins} min`;
  }

  function toast(message, kind = "ok") {
    const box = document.createElement("div");
    box.className = `toast ${kind === "ok" ? "" : kind}`;
    const mark = kind === "bad" ? "x" : kind === "info" ? "spark" : "check";
    box.innerHTML = `<span class="tick">${icon(mark)}</span><span>${esc(message)}</span>`;
    $("#toasts").appendChild(box);
    setTimeout(() => {
      box.classList.add("out");
      setTimeout(() => box.remove(), 240);
    }, 4200);
  }

  async function api(path, options = {}) {
    const opts = { headers: {}, ...options };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    } else if (opts.body) {
      opts.headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, opts);
    let payload = {};
    try { payload = await res.json(); } catch (_) { /* vacío */ }
    if (!res.ok || payload.ok === false) {
      throw new Error(payload.error || `Error ${res.status}`);
    }
    return payload;
  }

  /* --------------------------------------------------------- navegación */

  function setTab(name) {
    if (!TITLES[name]) return;
    state.tab = name;
    $$(".nav-item").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.tab === name));
    $$(".view").forEach((view) => view.classList.toggle("is-active", view.id === `view-${name}`));
    const [title, sub] = TITLES[name];
    $("#page-title").textContent = title;
    $("#page-sub").textContent = sub;
    if (name === "review") renderQueue();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ------------------------------------------------------------- estado */

  function renderVoice() {
    const vp = $("#voice-provider");
    if (vp && state.data.app) vp.value = state.data.app.voice_provider || "local";
    const ev = $("#edge-voice");
    if (ev && state.data.app) ev.value = state.data.app.edge_voice || "es-MX-DaliaNeural";
    const vt = $("#voice-tone");
    if (vt) vt.value = (state.data.settings && state.data.settings.voice_tone) || "breaking";
    const voice = state.data.voice;
    const chip = $("#voice-chip");
    if (voice.exists) {
      chip.textContent = `${voice.duration.toFixed(1)} s`;
      chip.className = "chip ok";
      $("#side-voice-dot").className = "dot ok";
      $("#side-voice-text").textContent = `Lista · ${voice.duration.toFixed(1)} s`;
      $("#voice-player").classList.remove("hidden");
      $("#voice-details").textContent = `${voice.duration.toFixed(1)} s · 24 kHz mono`;
      const audio = $("#voice-audio");
      if (audio.dataset.loaded !== voice.url) {
        audio.src = voice.url;
        audio.dataset.loaded = voice.url;
      }
      $("#voice-warning").classList.add("hidden");
    } else {
      chip.textContent = "Sin voz";
      chip.className = "chip bad";
      $("#side-voice-dot").className = "dot bad";
      $("#side-voice-text").textContent = "Falta tu referencia";
      $("#voice-player").classList.add("hidden");
      $("#voice-warning").classList.remove("hidden");
    }
  }

  function renderPills() {
    const { app, system, topics } = state.data;
    const engine = $("#pill-engine");
    const local = app.voice_provider === "local";
    engine.className = "pill";
    engine.innerHTML = `<i></i><span>Voz ${local ? "local" : "nube + local"}</span>`;

    const topicPill = $("#pill-topics");
    topicPill.className = "pill";
    topicPill.innerHTML = `<i></i><span>Vídeos ilimitados</span>`;

    const disk = $("#pill-disk");
    const free = system.disk_free_gb;
    disk.className = `pill${free < 5 ? " warn" : ""}`;
    disk.innerHTML = `<i></i><span>${free.toFixed(1)} GB libres</span>`;

    $("#stat-topics").textContent = topics.total;
    $("#stat-pending").textContent = state.data.queue.counts.pending;
    const lastRun = state.data.estimate >= 100 ? fmtMinutes(state.data.estimate) : "—";
    $("#stat-last").textContent = lastRun;
    const cloud = app.voice_provider !== "local";
    $("#estimate-hint").textContent =
      `Tarda ${fmtMinutes(state.data.estimate * state.count)}${cloud ? " con voz en la nube" : " con voz local"} · puedes seguir usando la app`;
    if (state.data.app.target_duration && !state.touchedDuration) {
      state.seconds = state.data.app.target_duration;
    }
    $$("#duration-seg button").forEach((b) => {
      b.classList.toggle("is-active", Number(b.dataset.value) === state.seconds);
    });
  }

  function renderTopics() {
    const input = $("#topic-input");
    if (!input) return;
    const all = state.data.topics.items || [];
    $("#topic-hint").textContent = `${all.length} temas disponibles · escribe uno propio o déjalo vacío para rotación automática`;
  }

  /* ------------------------------------------------ fuentes web y guion manual */

  function normalizeSource(raw) {
    let text = String(raw || "").trim();
    if (!text) return "";
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) text = "https://" + text;
    try {
      const url = new URL(text);
      if (!/^https?:$/i.test(url.protocol)) return "";
      url.hash = "";
      return url.toString();
    } catch (_) {
      return "";
    }
  }

  function persistSources() {
    try {
      localStorage.setItem("sourceUrls", JSON.stringify(state.sources));
    } catch (_) { /* noop */ }
  }

  function renderSources() {
    const list = $("#source-list");
    if (!list) return;
    list.innerHTML = "";
    state.sources.forEach((raw, index) => {
      const url = normalizeSource(raw) || raw;
      const chip = document.createElement("span");
      chip.className = "source-chip";
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = url;
      link.textContent = url.replace(/^https?:\/\//i, "").replace(/\/$/, "");
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.index = String(index);
      remove.title = "Quitar enlace";
      remove.setAttribute("aria-label", "Quitar enlace");
      remove.textContent = "×";
      chip.append(link, remove);
      list.appendChild(chip);
    });
  }

  function addSourcesFromInput() {
    const input = $("#source-input");
    if (!input) return;
    const tokens = input.value.split(/[\s,]+/).filter(Boolean);
    if (!tokens.length) return;
    let added = 0;
    let invalid = 0;
    let full = false;
    for (const token of tokens) {
      const url = normalizeSource(token);
      if (!url) { invalid += 1; continue; }
      if (state.sources.includes(url)) continue;
      if (state.sources.length >= 5) { full = true; break; }
      state.sources.push(url);
      added += 1;
    }
    input.value = "";
    if (invalid) toast("Ese enlace no es válido. Usa http o https.", "bad");
    if (full) toast("Máximo 5 enlaces por vídeo", "bad");
    if (added) {
      persistSources();
      renderSources();
      toast(added === 1 ? "Enlace añadido" : `${added} enlaces añadidos`);
    }
  }

  let scriptSaveTimer = null;

  function updateScriptMeta() {
    const input = $("#script-input");
    const out = $("#script-count");
    if (!input || !out) return;
    const text = input.value;
    const words = (text.match(/[\wáéíóúüñÁÉÍÓÚÜÑ]+/g) || []).length;
    const seconds = Math.round(words / 2.8);
    out.textContent = `${text.length.toLocaleString("es-ES")} caracteres · ~${seconds} s`;
    out.classList.toggle("is-over", seconds > 120);
  }

  function scheduleScriptSave(value) {
    if (scriptSaveTimer) clearTimeout(scriptSaveTimer);
    scriptSaveTimer = setTimeout(() => {
      try { localStorage.setItem("manualScript", value); } catch (_) { /* noop */ }
    }, 250);
  }

  function restoreCreateExtras() {
    const script = $("#script-input");
    if (script) {
      try { script.value = localStorage.getItem("manualScript") || ""; } catch (_) { /* noop */ }
      updateScriptMeta();
    }
    renderSources();
    renderResources();
  }

  /* ------------------------------------------------ recursos propios (Crear) */

  const RESOURCE_RE = /\.(jpe?g|png|webp|mp4|mov|mkv|webm|m4v)$/i;
  const YT_VIDEO_RE = /\.(mp4|mov|mkv|webm|m4v)$/i;
  const RESOURCE_MAX = 300 * 1024 * 1024;

  function persistResourceSelection() {
    try {
      localStorage.setItem("selectedResources", JSON.stringify(state.resourceSelection));
    } catch (_) { /* noop */ }
  }

  function availableResources() {
    return ((state.data && state.data.resources) || []);
  }

  function selectedResources() {
    const names = new Set(availableResources().map((r) => r.name));
    return state.resourceSelection.filter((name) => names.has(name));
  }

  function fmtBytes(bytes) {
    const mb = (Number(bytes) || 0) / (1024 * 1024);
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round((Number(bytes) || 0) / 1024))} KB`;
  }

  function pruneResourceSelection() {
    if (!state.data) return;
    const names = new Set(availableResources().map((r) => r.name));
    const kept = state.resourceSelection.filter((name) => names.has(name));
    if (kept.length !== state.resourceSelection.length) {
      state.resourceSelection = kept;
      persistResourceSelection();
    }
  }

  function toggleResource(name, force) {
    const has = state.resourceSelection.includes(name);
    const want = force === undefined ? !has : Boolean(force);
    if (want && !has) state.resourceSelection.push(name);
    if (!want && has) state.resourceSelection = state.resourceSelection.filter((n) => n !== name);
    persistResourceSelection();
    renderResources();
  }

  function renderResources() {
    const list = $("#resource-list");
    if (!list || !state.data) return;
    const items = availableResources();
    list.innerHTML = "";
    items.forEach((resource) => {
      const selected = state.resourceSelection.includes(resource.name);
      const card = document.createElement("div");
      card.className = "resource-card" + (selected ? "" : " is-off");
      card.dataset.name = resource.name;
      card.title = `${resource.name} · ${fmtBytes(resource.size)}`;
      const media = resource.kind === "image"
        ? `<img src="${esc(resource.url)}" alt="" loading="lazy">`
        : `<video src="${esc(resource.url)}" muted preload="metadata" playsinline></video>`;
      const duration = resource.duration ? ` · ${Math.round(resource.duration)}s` : "";
      card.innerHTML = `
        ${media}
        <span class="resource-check">${selected ? icon("check") : ""}</span>
        <button class="resource-del" type="button" data-del="${esc(resource.name)}" title="Borrar recurso">${icon("x")}</button>
        <span class="resource-meta">${esc(resource.name)}${duration}</span>`;
      list.appendChild(card);
    });
    const hint = $("#resource-hint");
    if (hint) {
      const chosen = selectedResources().length;
      if (!items.length) {
        hint.textContent = "Opcional: si no subes nada, se buscan fondos automáticamente.";
      } else if (!chosen) {
        hint.textContent = `${items.length} ${items.length === 1 ? "recurso guardado" : "recursos guardados"}, ninguno seleccionado: se buscan fondos automáticamente.`;
      } else {
        hint.textContent = `${chosen} de ${items.length} seleccionados · se usan en orden como base visual. Si generas varios shorts, solo el primero usa tus recursos.`;
      }
    }
  }

  async function uploadResourceFiles(fileList, opts = {}) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (state.resourceUploading) {
      toast("Espera a que termine la subida actual.", "info");
      return;
    }
    const forYt = opts.selection === "yt";
    const pattern = forYt ? YT_VIDEO_RE : RESOURCE_RE;
    const valid = files.filter((file) => pattern.test(file.name));
    if (!valid.length) {
      toast(forYt
        ? "Solo vídeos MP4/MOV/MKV/WEBM/M4V."
        : "Solo imágenes JPG/PNG/WEBP o vídeos MP4/MOV/MKV/WEBM/M4V.", "bad");
      return;
    }
    if (files.length !== valid.length) {
      toast(forYt
        ? "Algunos archivos no son vídeo compatible y se han saltado."
        : "Algunos archivos no son imagen o vídeo compatible y se han saltado.", "info");
    }
    if (availableResources().length + valid.length > 20) {
      toast("Máximo 20 recursos guardados. Borra alguno antes de subir más.", "bad");
      return;
    }
    const bar = $(opts.progress || "#resource-progress");
    const fill = bar ? bar.firstElementChild : null;
    if (bar) {
      bar.classList.remove("hidden");
      if (fill) fill.style.width = "2%";
    }
    state.resourceUploading = true;
    let done = 0;
    for (const file of valid) {
      if (file.size > RESOURCE_MAX) {
        toast(`${file.name}: pasa de 300 MB`, "bad");
        continue;
      }
      try {
        const payload = await new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest();
          xhr.open("POST", `/api/resources?name=${encodeURIComponent(file.name)}`);
          xhr.upload.onprogress = (ev) => {
            if (!fill || !ev.lengthComputable) return;
            const base = (done / valid.length) * 100;
            fill.style.width = `${Math.min(97, base + (ev.loaded / ev.total) * (100 / valid.length))}%`;
          };
          xhr.onload = () => {
            let data = {};
            try { data = JSON.parse(xhr.responseText); } catch (_) { /* noop */ }
            if (xhr.status === 200 && data.ok) resolve(data);
            else reject(new Error(data.error || "No se pudo subir el archivo"));
          };
          xhr.onerror = () => reject(new Error("Fallo de red al subir el archivo"));
          xhr.send(file);
        });
        const name = payload.resource && payload.resource.name;
        if (name) {
          if (forYt) {
            state.ytVideo = name;
          } else if (!state.resourceSelection.includes(name)) {
            state.resourceSelection.push(name);
          }
        }
        done += 1;
      } catch (err) {
        toast(`${file.name}: ${err.message}`, "bad");
      }
    }
    state.resourceUploading = false;
    if (fill) fill.style.width = "100%";
    if (bar) setTimeout(() => {
      bar.classList.add("hidden");
      if (fill) fill.style.width = "0";
    }, 600);
    if (forYt) persistYtVideo();
    else persistResourceSelection();
    await refresh();
    if (done) toast(done === 1 ? "Recurso añadido" : `${done} recursos añadidos`);
  }

  function deleteResource(name) {
    askConfirm(
      "¿Borrar este recurso?",
      "Se elimina del disco de tu equipo. No afecta a los shorts ya generados.",
      async () => {
        try {
          await api("/api/resources/delete", { method: "POST", body: { name } });
          state.resourceSelection = state.resourceSelection.filter((n) => n !== name);
          if (state.ytVideo === name) state.ytVideo = "";
          persistResourceSelection();
          persistYtVideo();
          toast("Recurso borrado");
          await refresh();
        } catch (err) { toast(err.message, "bad"); }
      },
    );
  }

  /* ------------------------------------------- vídeo manual (YouTube Clips) */

  function persistYtVideo() {
    try {
      localStorage.setItem("selectedYtVideo", state.ytVideo || "");
    } catch (_) { /* noop */ }
  }

  function availableYtVideos() {
    return availableResources().filter((r) => r.kind === "video");
  }

  function selectedYtVideo() {
    return availableYtVideos().some((r) => r.name === state.ytVideo) ? state.ytVideo : "";
  }

  function pruneYtVideo() {
    if (!state.data) return;
    const valid = selectedYtVideo();
    if (valid !== state.ytVideo) {
      state.ytVideo = valid;
      persistYtVideo();
    }
  }

  function toggleYtVideo(name) {
    state.ytVideo = selectedYtVideo() === name ? "" : name;
    persistYtVideo();
    renderYtVideos();
  }

  function renderYtVideos() {
    const list = $("#yt-video-list");
    if (!list || !state.data) return;
    const items = availableYtVideos();
    const chosen = selectedYtVideo();
    list.innerHTML = "";
    items.forEach((resource) => {
      const selected = chosen === resource.name;
      const card = document.createElement("div");
      card.className = "resource-card" + (selected ? "" : " is-off");
      card.dataset.name = resource.name;
      card.title = `${resource.name} · ${fmtBytes(resource.size)}`;
      const duration = resource.duration ? ` · ${Math.round(resource.duration)}s` : "";
      card.innerHTML = `
        <video src="${esc(resource.url)}" muted preload="metadata" playsinline></video>
        <span class="resource-check">${selected ? icon("check") : ""}</span>
        <button class="resource-del" type="button" data-del="${esc(resource.name)}" title="Borrar vídeo">${icon("x")}</button>
        <span class="resource-meta">${esc(resource.name)}${duration}</span>`;
      list.appendChild(card);
    });
    const hint = $("#yt-video-hint");
    if (hint) {
      if (!items.length) {
        hint.textContent = "Opcional: sin vídeo subido se usa el enlace de YouTube.";
      } else if (!chosen) {
        hint.textContent = `${items.length} ${items.length === 1 ? "vídeo guardado" : "vídeos guardados"}, ninguno elegido: se usa el enlace.`;
      } else {
        hint.textContent = `Vídeo elegido: ${chosen}. Se usa en lugar del enlace.`;
      }
    }
  }

  function updateAutoButton() {
    const btn = $("#btn-auto");
    if (!btn) return;
    btn.textContent = "";
    const svg = document.createElement("span");
    svg.innerHTML = '<svg><use href="#i-spark"/></svg>';
    btn.appendChild(svg.firstChild);
    btn.append(" Modo automático: " + (state.autoMode ? "ON" : "OFF"));
    btn.classList.toggle("btn-primary", state.autoMode);
    btn.classList.toggle("btn-ghost", !state.autoMode);
  }

  function renderBadge() {
    const pending = state.data.queue.counts.pending;
    const badge = $("#badge-pending");
    badge.textContent = pending;
    badge.classList.toggle("hidden", pending === 0);
    $("#count-pending").textContent = pending;
    $("#count-approved").textContent = state.data.queue.counts.approved;
    $("#count-published").textContent = state.data.queue.counts.published;
  }

  function renderSettings() {
    const { app, system, publish } = state.data;
    const keys = system.keys;
    const gfx = system.graphics || {};
    const providers = app.providers || [];
    const readyProvider = providers.find((p) => p.ready);
    const scriptText = app.script_provider === "template"
      ? "Plantillas locales"
      : readyProvider
        ? readyProvider.label
        : "Plantillas (falta clave)";
    const gfxText = app.graphics_provider === "hyperframes"
      ? (gfx.available ? `HyperFrames · ${gfx.templates} plantillas` : "HyperFrames (no disponible)")
      : "Degradados propios";
    const keyText = [
      keys.pexels ? "Pexels ✓" : "Pexels —",
      keys.pixabay ? "Pixabay ✓" : "Pixabay —",
      keys.gemini ? "Gemini ✓" : "Gemini —",
    ].join(" · ");

    const rows = [
      ["Nicho", esc(app.niche)],
      ["Motor de voz", app.voice_provider === "local" ? "Local (CPU)" : "Nube gratis + local"],
      ["Guiones", scriptText],
      ["Investigación", app.research ? "Sí · noticias y datos actuales" : "No"],
      ["Duración", `${app.min_duration || 60}-${app.max_duration || 120} s · ${app.target_duration || 90} s por defecto`],
      ["Gráficas", gfxText],
      ["Subtítulos", `Whisper ${system.whisper}${system.whisper_cached ? "" : " (se descarga)"}`],
      ["FFmpeg", system.ffmpeg ? "Incluido ✓" : "No encontrado"],
      ["Claves", keyText],
      ["Disco libre", `${system.disk_free_gb.toFixed(1)} GB`],
    ];
    $("#system-rows").innerHTML = rows
      .map(([k, v]) => `<div class="row"><span>${k}</span><span>${v}</span></div>`)
      .join("");

    $("#platforms").innerHTML = publish.platforms.length
      ? publish.platforms.map((p) => {
        const cls = p.state === "ready" ? "ok" : p.state === "auth" ? "warn" : "bad";
        return `<div class="platform"><span class="dot ${cls}"></span>
          <span class="name">${esc(p.name)}</span><span class="hint">${esc(p.hint)}</span></div>`;
      }).join("")
      : `<p class="hint">No hay plataformas activas en config.yaml.</p>`;
  }

  async function connectOAuth(provider) {
    const box = $("#api-rows");
    if (!box) return;
    const settings = state.data.settings || {};
    const keys = settings.keys || {};
    const labels = {
      GEMINI_API_KEY: "Gemini (Google AI)",
      OPENAI_API_KEY: "ChatGPT / OpenAI",
      GITHUB_TOKEN: "GitHub Models",
      PEXELS_API_KEY: "Pexels",
      PIXABAY_API_KEY: "Pixabay",
      UNSPLASH_ACCESS_KEY: "Unsplash",
      OPENVERSE_API_KEY: "Openverse (imagenes)",
      FACEBOOK_PAGE_TOKEN: "Facebook Page",
      ELEVENLABS_API_KEY: "ElevenLabs (voz)",
      ZERNIO_API_KEY: "Zernio (publicacion unificada)",
      NEWSAPI_KEY: "NewsAPI (noticias GTA/Rockstar)",
      IGDB_CLIENT_ID: "IGDB Client ID",
      IGDB_CLIENT_SECRET: "IGDB Client Secret",
      YOUTUBE_API_KEY: "YouTube Data API",
    };
    const order = Object.keys(labels);
    box.innerHTML = order.map((name) => {
      const set = !!keys[name];
      return `<div class="row" style="align-items:center;gap:10px">
        <span style="min-width:170px">${esc(labels[name])}</span>
        <input type="password" data-key="${esc(name)}" class="api-key-input" placeholder="${set ? "•••••• (guardada)" : "Pega tu clave aquí"}" style="flex:1;padding:6px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
        <span class="dot ${set ? "ok" : "bad"}" title="${set ? "Configurada" : "Sin configurar"}"></span>
      </div>`;
    }).join("");
  }

  async function saveApiKeys() {
    const inputs = $$(".api-key-input");
    const payload = {};
    inputs.forEach((inp) => {
      const v = inp.value.trim();
      if (v) payload[inp.dataset.key] = v;
    });
    if (!Object.keys(payload).length) {
      $("#keys-hint").textContent = "No hay claves nuevas que guardar.";
      return;
    }
    try {
      await api("/api/settings/keys", { method: "POST", body: payload });
      $("#keys-hint").textContent = "Claves guardadas. Recarga para aplicar.";
      inputs.forEach((inp) => { inp.value = ""; });
      await refresh();
    } catch (err) {
      $("#keys-hint").textContent = `Error: ${err.message}`;
    }
  }

  function renderApiKeys() {
    const box = $("#api-rows");
    if (!box) return;
    const settings = state.data.settings || {};
    const keys = settings.keys || {};
    const labels = {
      GEMINI_API_KEY: "Gemini (Google AI)",
      OPENAI_API_KEY: "ChatGPT / OpenAI",
      GITHUB_TOKEN: "GitHub Models",
      PEXELS_API_KEY: "Pexels",
      PIXABAY_API_KEY: "Pixabay",
      UNSPLASH_ACCESS_KEY: "Unsplash",
      OPENVERSE_API_KEY: "Openverse (imagenes)",
      FACEBOOK_PAGE_TOKEN: "Facebook Page",
      ELEVENLABS_API_KEY: "ElevenLabs (voz)",
      ZERNIO_API_KEY: "Zernio (publicacion unificada)",
      NEWSAPI_KEY: "NewsAPI (noticias GTA/Rockstar)",
      IGDB_CLIENT_ID: "IGDB Client ID",
      IGDB_CLIENT_SECRET: "IGDB Client Secret",
      YOUTUBE_API_KEY: "YouTube Data API",
    };
    box.innerHTML = Object.keys(labels).map((name) => {
      const set = !!keys[name];
      return `<div class="row" style="align-items:center;gap:10px">
        <span style="min-width:170px">${esc(labels[name])}</span>
        <input type="password" data-key="${esc(name)}" class="api-key-input" placeholder="${set ? "•••••• (guardada)" : "Pega tu clave aquí"}" style="flex:1;padding:6px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
        <span class="dot ${set ? "ok" : "bad"}"></span>
      </div>`;
    }).join("");
  }


  async function renderOAuth() {
    const box = $("#oauth-rows");
    const connBox = $("#oauth-connections");
    if (!box) return;
    let providers = [];
    let connections = [];
    try {
      const r = await api("/api/oauth/providers");
      providers = r.providers || [];
    } catch (e) { providers = []; }
    try {
      const r = await api("/api/oauth/connections");
      connections = r.connections || [];
    } catch (e) { connections = []; }
    box.innerHTML = providers.map((p) => {
      const configured = p.configured;
      return `<div class="row" style="align-items:center;gap:10px">
        <span style="min-width:170px">${esc(p.label || p.id)}</span>
        <span class="dot ${configured ? "ok" : "bad"}"></span>
        <button class="btn btn-ghost btn-sm" data-oauth-connect="${esc(p.id)}" ${configured ? "" : "disabled"}>Conectar</button>
      </div>`;
    }).join("") || `<p class="hint">Sin proveedores OAuth configurados.</p>`;
    if (connBox) {
      connBox.innerHTML = connections.length
        ? connections.map((c) => `<div class="row"><span>${esc(c.provider || "")}</span><span>${esc(c.username || c.accountId || "")}</span></div>`).join("")
        : `<p class="hint">Sin cuentas conectadas.</p>`;
    }
  }

  async function renderAutopilot() {
    const box = document.querySelector("#autopilot-rows");
    if (!box) return;
    let data = {};
    try { data = await api("/api/autopilot"); } catch (e) { data = {}; }
    const cfg = data.config || {};
    box.innerHTML = `
      <div class="row"><span>Activo</span><span>${cfg.enabled ? "Si" : "No"}</span></div>
      <div class="row"><span>Videos por dia</span><span>${cfg.daily_count || 1}</span></div>
      <div class="row"><span>Duracion</span><span>${cfg.duration || 90} s</span></div>
      <div class="row"><span>Aprobacion</span><span>${cfg.approval_mode || "manual"}</span></div>
      <div class="row"><span>Horas</span><span>${(cfg.schedule_hours || []).join(", ")}</span></div>
      <div class="row"><span>Ejecuciones registradas</span><span>${(data.runs || []).length}</span></div>
      <div class="row-actions" style="margin-top:10px">
        <button class="btn btn-primary btn-sm" id="btn-autopilot-run">Ejecutar ahora</button>
        <span class="hint">${data.due ? "Hay una ejecucion pendiente" : "Sin ejecuciones pendientes"}</span>
      </div>`;
    const btn = document.querySelector("#btn-autopilot-run");
    if (btn) btn.onclick = async () => {
      try { const r = await api("/api/autopilot/run", { method: "POST", body: {} }); toast(r.ok ? "Piloto ejecutado" : (r.reason || r.error || "Fallo"), r.ok ? "good" : "bad"); await refresh(); } catch (e) { toast(e.message, "bad"); }
    };
  }

  function renderAI() {
    const box = document.querySelector("#ai-rows");
    if (!box) return;
    const app = state.data.app;
    const system = state.data.system;
    const providers = app.providers || [];
    const rows = [
      ["Proveedor de guion", app.script_provider || "auto"],
      ["Investigacion", app.research ? "Si - noticias y datos actuales" : "No"],
      ["Duracion", (app.min_duration || 60) + "-" + (app.max_duration || 120) + " s - " + (app.target_duration || 90) + " s por defecto"],
      ["Gemini", system.keys.gemini ? "Configurado" : "Sin clave"],
      ["OpenAI", system.keys.openai ? "Configurado" : "Sin clave"],
      ["GitHub Models", system.keys.github ? "Configurado" : "Sin clave"],
    ];
    box.innerHTML = rows.map(([k,v]) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("")
      + `<p class="hint" style="margin-top:8px">Proveedores listos: ${providers.filter(p=>p.ready).map(p=>p.label).join(", ") || "ninguno (se usan plantillas)"}</p>`;
    const sel = $("#script-provider");
    if (sel) sel.value = app.script_provider || "auto";
    const hint = $("#script-provider-hint");
    if (hint) {
      const ready = providers.find((p) => p.id === (app.script_provider || "auto"));
      const anyReady = providers.filter((p) => p.ready).map((p) => p.label).join(", ");
      hint.textContent = app.script_provider === "template"
        ? "Se usarán plantillas locales (sin IA)."
        : (anyReady ? "Listos: " + anyReady : "Falta una clave de IA. Pega la de OpenAI abajo.");
    }
  }

  function renderSecurity() {
    const box = document.querySelector("#security-rows");
    if (!box) return;
    const settings = state.data.settings || {};
    const keys = settings.keys || {};
    const configured = Object.values(keys).filter(Boolean).length;
    const rows = [
      ["Claves configuradas", configured + " de " + Object.keys(keys).length],
      ["Tokens OAuth", "Cifrados en disco (Fernet)"],
      ["Renovacion de tokens", "Automatica al expirar"],
      ["Revocacion", "Desde Redes sociales - Desconectar"],
    ];
    box.innerHTML = rows.map(([k,v]) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("");
  }

  function renderStatus() {
    const app = state.data.app;
    const system = state.data.system;
    const publish = state.data.publish;
    const keys = system.keys;
    const gfx = system.graphics || {};
    const gfxText = app.graphics_provider === "hyperframes"
      ? (gfx.available ? "HyperFrames - " + gfx.templates + " plantillas" : "HyperFrames (no disponible)")
      : "Degradados propios";
    const readyProvider = (app.providers || []).find(p => p.ready);
    const rows = [
      ["Nicho", esc(app.niche)],
      ["Motor de voz", app.voice_provider === "local" ? "Local (CPU)" : "Nube gratis + local"],
      ["Guiones", app.script_provider === "template" ? "Plantillas" : (readyProvider ? readyProvider.label : "Falta clave")],
      ["Graficas", gfxText],
      ["Subtitulos", "Whisper " + system.whisper + (system.whisper_cached ? "" : " (se descarga)")],
      ["FFmpeg", system.ffmpeg ? "Incluido" : "No encontrado"],
      ["Claves", [keys.pexels?"Pexels":"Pexels -", keys.pixabay?"Pixabay":"Pixabay -", keys.gemini?"Gemini":"Gemini -", keys.openai?"OpenAI":"OpenAI -", keys.github?"GitHub":"GitHub -"].join(" - ")],
      ["Disco libre", system.disk_free_gb.toFixed(1) + " GB"],
    ];
    const sr = document.querySelector("#system-rows");
    if (sr) sr.innerHTML = rows.map(([k,v]) => `<div class="row"><span>${esc(k)}</span><span>${esc(v)}</span></div>`).join("");
    const pl = document.querySelector("#platforms");
    if (pl) pl.innerHTML = publish.platforms.length
      ? publish.platforms.map((p) => {
        const cls = p.state === "ready" ? "ok" : p.state === "auth" ? "warn" : "bad";
        return `<div class="platform"><span class="dot ${cls}"></span>
          <span class="name">${esc(p.name)}</span><span class="hint">${esc(p.hint)}</span></div>`;
      }).join("")
      : `<p class="hint">No hay plataformas activas en config.yaml.</p>`;
  }


  async function renderGTA() {
    // noop placeholder; results rendered on search
  }

  async function gtaSearch() {
    const q = ($("#gta-query") || {}).value || "GTA 6";
    const sort = ($("#gta-sort") || {}).value || "relevance";
    const status = $("#gta-status");
    if (status) status.textContent = "Buscando...";
    try {
      const data = await api(`/api/gta/search?q=${encodeURIComponent(q)}&sort=${encodeURIComponent(sort)}`);
      const newsBox = $("#gta-news");
      const imgBox = $("#gta-images");
      const vidBox = $("#gta-videos");
      if (newsBox) {
        newsBox.innerHTML = (data.news || []).length
          ? data.news.map((n) => `<div class="row" style="flex-direction:column;align-items:flex-start;gap:4px">
              <strong>${esc(n.title)}</strong>
              <span class="hint">${esc(n.source)} · ${esc(n.publishedAt || "")}</span>
              ${n.description ? `<p class="hint">${esc(n.description)}</p>` : ""}
              ${n.url ? `<a href="${esc(n.url)}" target="_blank" class="hint">Ver fuente</a>` : ""}
            </div>`).join("")
          : `<p class="hint">Sin noticias.</p>`;
      }
      if (imgBox) {
        imgBox.innerHTML = (data.images || []).length
          ? data.images.map((g) => `<div class="row" style="flex-direction:column;align-items:flex-start;gap:4px">
              <strong>${esc(g.title)}</strong>
              ${g.description ? `<p class="hint">${esc(g.description)}</p>` : ""}
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                ${(g.images || []).slice(0,4).map((u) => `<img src="${esc(u)}" style="width:120px;height:68px;object-fit:cover;border-radius:6px">`).join("")}
              </div>
            </div>`).join("")
          : `<p class="hint">Sin imágenes.</p>`;
      }
      if (vidBox) {
        vidBox.innerHTML = (data.videos || []).length
          ? data.videos.map((v) => `<div class="row" style="flex-direction:column;align-items:flex-start;gap:4px">
              <strong>${esc(v.title)}</strong>
              <span class="hint">${esc(v.channel)} · ${esc(v.publishedAt || "")}</span>
              ${v.thumbnail ? `<img src="${esc(v.thumbnail)}" style="width:160px;height:90px;object-fit:cover;border-radius:6px">` : ""}
              ${v.url ? `<a href="${esc(v.url)}" target="_blank" class="hint">Ver en YouTube</a>` : ""}
            </div>`).join("")
          : `<p class="hint">Sin videos.</p>`;
      }
      if (status) status.textContent = `Resultados: ${data.counts.news} noticias, ${data.counts.images} imágenes, ${data.counts.videos} videos.`;
    } catch (e) {
      if (status) status.textContent = "Error: " + e.message;
    }
  }

  async function renderZernio() {
    const rows = document.querySelector("#zernio-rows");
    const accounts = document.querySelector("#zernio-accounts");
    const hint = document.querySelector("#zernio-hint");
    if (!rows) return;
    let status = { configured: false, accounts: [] };
    try { status = await api("/api/zernio/status"); } catch (e) { status = { configured: false, accounts: [] }; }
    rows.innerHTML = `<div class="row"><span>Estado</span><span>${status.verified ? "Conectado" : (status.configured ? "Key guardada (no verificada)" : "Desconectado")}</span></div>
      <div class="row"><span>Cuentas</span><span>${(status.accounts || []).length}</span></div>
      ${status.verify_error ? `<div class="row"><span>Error</span><span>${esc(status.verify_error)}</span></div>` : ""}`;
    if (accounts) {
      accounts.innerHTML = (status.accounts || []).length
        ? status.accounts.map((a) => `<div class="platform" style="padding:8px 0">
            <span class="dot ok"></span>
            <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;background:#000;color:#fff;font-weight:700;font-size:13px;margin-right:8px">Z</span>
            <span class="name">${esc(a.platform || "Zernio")}</span>
            <span class="hint">${esc(a.username || a.accountId || "")}</span>
            <button class="btn btn-primary btn-sm" style="margin-left:auto;background:#16a34a;border-color:#16a34a">Conectado</button>
          </div>`).join("")
        : `<p class="hint">Sin cuentas conectadas. Conectalas en el panel de Zernio.</p>`;
    }
    const saveBtn = document.querySelector("#btn-zernio-save");
    const testBtn = document.querySelector("#btn-zernio-test");
    if (saveBtn) saveBtn.onclick = async () => {
      const val = (document.querySelector("#zernio-key-input") || {}).value || "";
      if (!val.trim()) { if (hint) hint.textContent = "Pega tu API key primero."; return; }
      try {
        await api("/api/zernio/key", { method: "POST", body: { api_key: val.trim() } });
        if (hint) hint.textContent = "API key guardada.";
        const inp = document.querySelector("#zernio-key-input");
        if (inp) inp.value = "";
        await refresh();
      } catch (e) { if (hint) hint.textContent = "Error: " + e.message; }
    };
    if (testBtn) testBtn.onclick = async () => {
      if (hint) hint.textContent = "Probando...";
      try {
        const r = await api("/api/zernio/test", { method: "POST", body: {} });
        if (r.ok) { if (hint) hint.textContent = "Conexion OK - " + (r.accounts || []).length + " cuentas."; await refresh(); }
        else if (hint) hint.textContent = "Fallo: " + (r.error || "sin detalles");
      } catch (e) { if (hint) hint.textContent = "Error: " + e.message; }
    };
  }

  function renderGraphics() {
    const box = $("#graphics-tags");
    const hint = $("#graphics-hint");
    if (!box) return;
    const g = (state.data.system && state.data.system.graphics) || {};
    const items = g.items || [];
    if (!g.available) {
      box.innerHTML = `<span class="tag">HyperFrames no disponible · se usan degradados propios</span>`;
      hint.textContent = "";
      $("#btn-graphics-more").disabled = true;
      return;
    }
    box.innerHTML = items.length
      ? items.map((i) => `<span class="tag">${esc(i.title || i.id)}</span>`).join("")
      : `<span class="tag">Sin plantillas</span>`;
    const missing = (g.suggested || []).filter((n) => !(g.names || []).includes(n));
    hint.textContent = missing.length
      ? `${g.templates} instaladas · ${missing.length} más en el catálogo`
      : `${g.templates} instaladas · no queda ninguna sugerida`;
    $("#btn-graphics-more").disabled = false;
  }

  async function installGraphics() {
    const g = (state.data.system && state.data.system.graphics) || {};
    const missing = (g.suggested || [])
      .filter((n) => !(g.names || []).includes(n))
      .slice(0, 3);
    if (!missing.length) {
      toast("Ya tienes todas las plantillas sugeridas.", "info");
      return;
    }
    const btn = $("#btn-graphics-more");
    btn.disabled = true;
    toast(`Descargando ${missing.length} plantilla(s) de HyperFrames…`, "info");
    let done = 0;
    for (const name of missing) {
      try {
        await api("/api/graphics/install", { method: "POST", body: { name } });
        done += 1;
        toast(`Plantilla lista: ${name}`);
      } catch (err) {
        toast(`${name}: ${err.message}`, "bad");
      }
    }
    btn.disabled = false;
    await refresh();
    if (done) toast("Se estrenarán al generar los próximos vídeos.");
  }

  function renderFrames() {
    const grid = $("#frame-grid");
    if (!grid || !state.data) return;
    const s = state.data.settings || {};
    const current = s.frame_template || "";
    const choices = [{ key: "", label: "Ninguno", file: "", available: true }]
      .concat(Array.isArray(s.frames) ? s.frames : []);
    const signature = choices.map((c) => `${c.key}:${c.available}:${c.version || 0}`).join("|");
    if (grid.dataset.sig !== signature) {
      grid.innerHTML = "";
      choices.forEach((c) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "frame-card";
        card.dataset.frame = c.key || "";
        card.disabled = !c.available;
        const thumb = document.createElement("span");
        thumb.className = "frame-thumb";
        if (c.file) {
          const img = document.createElement("img");
          img.src = "/frames/" + c.file + (c.version ? "?v=" + c.version : "");
          img.alt = "Marco " + c.label;
          img.decoding = "async";
          thumb.appendChild(img);
        } else {
          thumb.classList.add("frame-thumb-none");
          thumb.textContent = "Vídeo limpio";
        }
        const name = document.createElement("strong");
        name.textContent = c.label;
        const st = document.createElement("span");
        st.className = "frame-state";
        card.append(thumb, name, st);
        grid.appendChild(card);
      });
      grid.dataset.sig = signature;
    }
    grid.querySelectorAll(".frame-card").forEach((card) => {
      const on = (card.dataset.frame || "") === current;
      card.classList.toggle("is-active", on);
      const st = card.querySelector(".frame-state");
      if (st) st.textContent = on ? "Activo" : (card.disabled ? "Falta el PNG" : "Usar");
      card.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const hint = $("#frame-hint");
    if (hint) {
      const active = choices.find((c) => c.key === current);
      hint.textContent = current
        ? `Marco activo: ${active ? active.label : current}. Se aplicará al próximo vídeo.`
        : "Sin marco: el vídeo sale limpio. Elige una plantilla para el próximo vídeo.";
    }
  }

  async function renderAll() {
    if (!state.data) return;
    $("#version").textContent = `v${state.data.app.version}`;
    renderVoice();
    const bs = $("#bg-source");
    if (bs && state.data.settings) bs.value = state.data.settings.media_source || state.data.app.media_source || "mixed";
    const mv = $("#music-volume");
    if (mv && state.data.settings) {
      const s = state.data.settings;
      const setRange = (id, key, fmt) => {
        const el = $(id);
        if (!el || s[key] == null) return;
        el.value = s[key];
        const label = $(id + "-val");
        if (label) label.textContent = fmt(el.value);
      };
      mv.value = s.music_db;
      setRange("#music-intro-db", "music_intro_db",
        (v) => (Number(v) >= 0 ? "+" : "") + Number(v) + " dB");
      setRange("#music-fade-in", "music_fade_in", (v) => Number(v).toFixed(2) + " s");
      setRange("#music-intro-fade", "music_intro_fade", (v) => Number(v).toFixed(2) + " s");
      const mvl = $("#music-volume-val");
      if (mvl) {
        const db = Number(s.music_db);
        mvl.textContent = (db >= 0 ? "+" : "") + db + " dB";
      }
    }
    renderFrames();
    const vi = $("#voice-intensity");
    if (vi && state.data.settings && state.data.settings.voice_intensity != null) {
      vi.value = state.data.settings.voice_intensity;
      const vil = $("#voice-intensity-val");
      if (vil) vil.textContent = (state.data.settings.voice_intensity >= 0 ? "+" : "") + state.data.settings.voice_intensity + "%";
    }
    renderPills();
    renderTopics();
    renderSources();
    renderResources();
    renderYtVideos();
    renderBadge();
    renderQueue();
    renderStatus();
    renderGraphics();
    renderApiKeys();
    try { await renderOAuth(); } catch (e) { /* noop */ }
    await renderAutopilot();
    renderAI();
    renderSecurity();
    await renderZernio();
  }

  /* -------------------------------------------------------------- cola */

  function cardHTML(item) {
    const cover = item.cover_url
      ? `<img src="${item.cover_url}" alt="" loading="lazy">`
      : `<div class="thumb-fallback"></div>`;
    return `<article class="short-card" data-id="${esc(item.id)}" data-status="${item.status}">
      <button class="thumb" data-open="${esc(item.id)}" title="Ver ${esc(item.title)}">
        ${cover}
        <span class="state-dot"></span>
        <span class="play"><span>${icon("play")}</span></span>
        <span class="dur">${fmtTime(item.duration)}</span>
      </button>
      <div class="short-info">
        <h4>${esc(item.title)}</h4>
        <div class="short-foot">
          <span>${STATUS_LABEL[item.status]}</span>
          <button class="icon-btn" data-folder="${esc(item.id)}" title="Abrir carpeta">${icon("folder")}</button>
        </div>
      </div>
    </article>`;
  }

  function renderQueue() {
    if (!state.data) return;
    const status = state.queueTab;
    const items = state.data.queue[status] || [];
    const grid = $("#queue-grid");
    grid.innerHTML = items.map(cardHTML).join("");
    const empty = $("#queue-empty");
    empty.classList.toggle("hidden", items.length > 0);
    grid.classList.toggle("hidden", items.length === 0);
    $("#empty-text").textContent = EMPTY_TEXT[status];
    $$("#queue-seg button").forEach((b) => b.classList.toggle("is-active", b.dataset.value === status));
    $("#btn-approve-all").disabled = state.data.queue.counts.pending === 0;
    $("#btn-publish").disabled = state.data.queue.counts.approved === 0;
    $("#btn-publish-dry").disabled = state.data.queue.counts.approved === 0;
  }

  function findItem(id) {
    const q = state.data.queue;
    return [...q.pending, ...q.approved, ...q.published].find((i) => i.id === id) || null;
  }

  function openModal(id) {
    const item = findItem(id);
    if (!item) return;
    state.modalItem = item;
    $("#modal-title").textContent = item.title;
    $("#modal-desc").textContent = item.description || item.hook || "Sin descripción.";
    $("#modal-script-text").textContent = item.text || "—";
    $("#modal-duration").textContent = fmtTime(item.duration);
    const created = item.created_at ? ` · Creado: ${item.created_at}` : "";
    $("#modal-desc").textContent = (item.description || item.hook || "Sin descripción.") + created;
    const badge = $("#modal-badge");
    badge.textContent = STATUS_LABEL[item.status];
    badge.className = `chip ${item.status === "pending" ? "warn" : item.status === "approved" ? "ok" : ""}`;
    $("#modal-tags").innerHTML = (item.hashtags || []).map((h) => `<span class="tag">${esc(h)}</span>`).join("");
    const video = $("#modal-video");
    video.src = item.video_url || "";
    video.poster = item.cover_url || "";
    const approve = $("#modal-approve");
    const reject = $("#modal-reject");
    approve.classList.toggle("hidden", item.status !== "pending");
    reject.classList.toggle("hidden", item.status !== "pending");
    $("#modal-folder").dataset.rel = `queue/${item.status}/${item.id}`;
    $("#modal").classList.remove("hidden");
    $("#modal").setAttribute("aria-hidden", "false");
    setTimeout(() => video.play().catch(() => {}), 120);
  }

  function closeModal() {
    const video = $("#modal-video");
    video.pause();
    video.removeAttribute("src");
    $("#modal").classList.add("hidden");
    $("#modal").setAttribute("aria-hidden", "true");
    state.modalItem = null;
  }

  /* --------------------------------------------------------- trabajos */

  function setJobView(job) {
    state.job = job;
    const running = job.running;
    const done = !running && job.ok === true && job.finished > 0 && Math.abs(job.finished - state.dismissed) > 0.5;
    const failed = !running && job.ok === false && job.finished > 0 && Math.abs(job.finished - state.dismissed) > 0.5;

    $("#job-idle").classList.toggle("hidden", running || done || failed);
    $("#job-run").classList.toggle("hidden", !running);
    $("#job-done").classList.toggle("hidden", !done);
    $("#job-fail").classList.toggle("hidden", !failed);
    $("#btn-run").disabled = running || !state.data.voice.exists;

    if (running) {
      $("#job-label").textContent = job.label || "Generando…";
      $("#job-time").textContent = fmtTime(job.elapsed);
      $("#progress-fill").style.width = `${Math.min(99, job.percent)}%`;
      const names = ["Guion", "Voz clonada", "Fondos", "Subtítulos", "Montaje", "En cola"];
      $("#job-step").textContent = job.step
        ? `Paso ${job.step} de 6 · ${job.step_name || names[job.step - 1]}`
        : "Arrancando…";
      const left = Math.max(0, (job.estimate || 420) - job.elapsed);
      $("#job-eta").textContent = job.elapsed < 8 ? "" : `quedan ~${fmtTime(left)}`;
      $$("#steps li").forEach((li) => {
        const n = Number(li.dataset.step);
        li.classList.toggle("is-done", n < job.step);
        li.classList.toggle("is-active", n === job.step);
      });
      const log = $("#log");
      const text = job.lines.join("\n");
      if (log.textContent !== text) {
        const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
        log.textContent = text;
        if (stick) log.scrollTop = log.scrollHeight;
      }
    } else if (done) {
      $("#job-done-sub").textContent = `Ya está en Revisar. Duración del proceso: ${fmtTime(
        (job.finished || 0) - (job.started || 0))}.`;
    } else if (failed) {
      $("#job-fail-sub").textContent = (job.error || "Revisa el detalle.").slice(0, 220);
      $("#fail-log").textContent = (job.lines || []).slice(-40).join("\n");
    }
    scheduleNext(running);
  }

  function scheduleNext(running) {
    clearTimeout(state.timer);
    const fast = running;
    state.timer = setTimeout(tick, fast ? 1200 : 4000);
  }

  async function tick() {
    try {
      const data = await api("/api/state");
      const wasRunning = state.job?.running;
      state.data = data;
      renderAll();
      setJobView(data.job);
      if (wasRunning && !data.job.running) {
        if (data.job.ok) {
          toast("Short generado y listo para revisar");
          if (state.autoMode) setTimeout(autoStart, 1500);
        } else if (data.job.error !== "Cancelado por ti.") {
          toast(data.job.error || "No se pudo terminar", "bad");
          if (state.autoMode) setTimeout(autoStart, 5000);
        }
      }
    } catch (err) {
      // el servidor puede estar apagándose; reintenta sin spamear
      state.timer = setTimeout(tick, 5000);
    }
  }

  /* --------------------------------------------------------- acciones */

  async function autoStart() {
    if (!state.autoMode) return;
    if (!state.data || !state.data.voice.exists) return;
    if (state.job && state.job.running) return;
    try {
      const res = await api("/api/run", {
        method: "POST",
        body: { count: 1, topic: "", seconds: 90 },
      });
      state.dismissed = 0;
      state.data.job = res.job;
      setJobView(res.job);
      toast("Modo automático: generando short…", "info");
    } catch (err) {
      toast(err.message, "bad");
      // retry later
      setTimeout(() => { if (state.autoMode) autoStart(); }, 5000);
    }
  }

  async function toggleAutoMode() {
    state.autoMode = !state.autoMode;
    localStorage.setItem("autoMode", state.autoMode ? "1" : "0");
    updateAutoButton();
    // persist to autopilot config
    try {
      await api("/api/autopilot", { method: "POST", body: { enabled: state.autoMode } });
    } catch (_) { /* noop */ }
    if (state.autoMode) {
      toast("Modo automático activado", "ok");
      autoStart();
    } else {
      toast("Modo automático desactivado", "info");
    }
  }

  async function startRun() {
    if (!state.data.voice.exists) {
      toast("Primero sube tu voz de referencia", "bad");
      setTab("voice");
      return;
    }
    try {
      const topic = ($("#topic-input")?.value || "").trim();
      const script = ($("#script-input")?.value || "").trim();
      const res = await api("/api/run", {
        method: "POST",
        body: {
          count: state.count,
          topic,
          seconds: state.seconds,
          sources: state.sources,
          script,
          resources: selectedResources(),
        },
      });
      state.dismissed = 0;
      state.data.job = res.job;
      setJobView(res.job);
      toast(`Generando ${state.count} short${state.count > 1 ? "s" : ""}…`, "info");
    } catch (err) {
      toast(err.message, "bad");
    }
  }

  async function approve(id) {
    try {
      await api("/api/approve", { method: "POST", body: { id } });
      toast("Aprobado. Listo para publicar.");
      closeModal();
      await refresh();
    } catch (err) { toast(err.message, "bad"); }
  }

  function askConfirm(title, text, onYes, danger = true) {
    $("#confirm-title").textContent = title;
    $("#confirm-text").textContent = text;
    const yes = $("#confirm-yes");
    yes.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    const handler = async () => {
      cleanup();
      await onYes();
    };
    const cleanup = () => {
      $("#confirm").classList.add("hidden");
      yes.removeEventListener("click", handler);
    };
    yes.addEventListener("click", handler);
    $("#confirm-no").onclick = cleanup;
    $("#confirm").classList.remove("hidden");
  }

  async function reject(id) {
    askConfirm("¿Descartar este short?", "Se borra de la cola. Esta acción no se puede deshacer.", async () => {
      try {
        await api("/api/reject", { method: "POST", body: { id } });
        toast("Descartado");
        closeModal();
        await refresh();
      } catch (err) { toast(err.message, "bad"); }
    });
  }

  async function deleteShort(id) {
    askConfirm("¿Eliminar este short?", "Se borra definitivamente de la cola (pendiente, aprobado o publicado).", async () => {
      try {
        const res = await api("/api/delete", { method: "POST", body: { id } });
        if (!res.ok) throw new Error("No se pudo eliminar.");
        toast("Eliminado");
        closeModal();
        await refresh();
      } catch (err) { toast(err.message, "bad"); }
    });
  }

  async function publish(dryRun) {
    const go = async () => {
      try {
        const res = await api("/api/publish", { method: "POST", body: { dry_run: dryRun } });
        state.dismissed = 0;
        setJobView(res.job);
        toast(dryRun ? "Simulando publicación…" : "Publicando…", "info");
      } catch (err) { toast(err.message, "bad"); }
    };
    if (dryRun) { go(); return; }
    askConfirm(
      "¿Publicar ahora?",
      "Se subirán los shorts aprobados a las plataformas conectadas. Si algo falla, se quedan en Aprobados.",
      go, false,
    );
  }

  async function uploadVoice(file) {
    if (!file) return;
    if (!file.type.startsWith("audio") && !/\.(m4a|mp3|wav|ogg|aac|flac)$/i.test(file.name)) {
      toast("Ese archivo no parece audio", "bad");
      return;
    }
    state.uploading = true;
    const bar = $("#drop-progress");
    const fill = bar.firstElementChild;
    bar.classList.remove("hidden");
    fill.style.width = "4%";

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/voice?name=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (ev) => {
      if (ev.lengthComputable) fill.style.width = `${Math.max(4, (ev.loaded / ev.total) * 92)}%`;
    };
    xhr.onload = async () => {
      state.uploading = false;
      fill.style.width = "100%";
      let payload = {};
      try { payload = JSON.parse(xhr.responseText); } catch (_) { /* noop */ }
      setTimeout(() => bar.classList.add("hidden"), 500);
      if (xhr.status === 200 && payload.ok) {
        toast(`Voz lista · ${payload.duration.toFixed(1)} s`);
        await refresh();
      } else {
        toast(payload.error || "No se pudo preparar la voz", "bad");
      }
    };
    xhr.onerror = () => {
      state.uploading = false;
      bar.classList.add("hidden");
      toast("Fallo de red al subir el audio", "bad");
    };
    xhr.send(file);
  }

  async function pollYtClips() {
    const prog = $("#yt-progress");
    const res = $("#yt-results");
    const t0 = Date.now();
    let tries = 0;
    const tick = async () => {
      try {
        const st = await api("/api/youtube-clips/status");
        const elapsed = Math.floor((Date.now() - t0) / 1000);
        if (prog) prog.textContent = (st.progress || "") + " · " + elapsed + "s";
        if (res && Array.isArray(st.results) && st.results.length) {
          res.innerHTML = st.results.map((r) => {
            if (r.error) return `<div class="row"><span>${esc(r.title || "Clip")}</span><span class="bad">${esc(r.error)}</span></div>`;
            const rel = (r.path || "").replace(/\\/g, "/").replace(/^.*?\.cache/, ".cache");
            const mediaUrl = rel ? `/media?rel=${encodeURIComponent(rel)}` : "";
            const created = r.created_at ? ` · ${esc(r.created_at)}` : "";
            return `<div class="card" style="margin-top:10px;padding:10px">
              <div style="display:flex;gap:12px;align-items:flex-start">
                ${mediaUrl ? `<video src="${mediaUrl}" controls style="width:160px;border-radius:8px" preload="metadata"></video>` : ""}
                <div style="flex:1">
                  <strong>${esc(r.title)}</strong>
                  <p class="hint" style="margin:4px 0">${esc(r.reason || "")}${created}</p>
                  <span class="mono muted">${r.duration || ""}s · ${fmtTime(r.start || 0)}–${fmtTime(r.end || 0)}</span>
                  <div style="margin-top:8px">
                    <button class="btn btn-danger btn-sm" data-yt-delete="${esc(r.path || "")}">Eliminar</button>
                  </div>
                </div>
              </div>
            </div>`;
          }).join("");
          res.querySelectorAll("[data-yt-delete]").forEach((btn) => {
            btn.addEventListener("click", async () => {
              try {
                await api("/api/youtube-clips/delete", { method: "POST", body: { path: btn.dataset.ytDelete } });
                toast("Clip eliminado");
                pollYtClips();
              } catch (e) { toast(e.message, "bad"); }
            });
          });
        }
        if (st.running) {
          if (tries < 600) { tries++; setTimeout(tick, 2000); }
        } else {
          const elapsed = Math.floor((Date.now() - t0) / 1000);
          if (prog) prog.textContent = (st.progress || "Listo") + " · " + elapsed + "s";
        }
      } catch (e) { if (prog) prog.textContent = `Error: ${e.message}`; }
    };
    tick();
  }

  async function refresh() {
    try {
      state.data = await api("/api/state");
      pruneResourceSelection();
      pruneYtVideo();
      renderAll();
      setJobView(state.data.job);
    } catch (_) { /* noop */ }
  }

  /* ------------------------------------------------------------ eventos */

  function bind() {
    $("#nav").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".nav-item");
      if (btn) setTab(btn.dataset.tab);
    });

    document.addEventListener("click", (ev) => {
      const tabBtn = ev.target.closest("[data-tab]");
      if (tabBtn && !tabBtn.classList.contains("nav-item")) setTab(tabBtn.dataset.tab);
    });

    $("#duration-seg").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-value]");
      if (!btn) return;
      state.seconds = Number(btn.dataset.value);
      state.touchedDuration = true;
      $$("#duration-seg button").forEach((b) => b.classList.toggle("is-active", b === btn));
    });

    $("#count-seg").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-value]");
      if (!btn) return;
      state.count = Number(btn.dataset.value);
      $$("#count-seg button").forEach((b) => b.classList.toggle("is-active", b === btn));
      $("#estimate-hint").textContent =
        `Tarda ${fmtMinutes(state.data.estimate * state.count)} con voz local · puedes seguir usando la app`;
    });

    $("#btn-run").addEventListener("click", startRun);

    const sourceBtn = $("#btn-add-source");
    if (sourceBtn) sourceBtn.addEventListener("click", addSourcesFromInput);
    const sourceInput = $("#source-input");
    if (sourceInput) sourceInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        addSourcesFromInput();
      }
    });
    const sourceList = $("#source-list");
    if (sourceList) sourceList.addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-index]");
      if (!btn) return;
      state.sources.splice(Number(btn.dataset.index), 1);
      persistSources();
      renderSources();
    });
    const resourceZone = $("#resource-drop");
    const resourceInput = $("#resource-input");
    if (resourceZone && resourceInput) {
      resourceZone.addEventListener("click", () => resourceInput.click());
      resourceZone.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          resourceInput.click();
        }
      });
      resourceInput.addEventListener("change", () => {
        uploadResourceFiles(resourceInput.files);
        resourceInput.value = "";
      });
      ["dragenter", "dragover"].forEach((name) => resourceZone.addEventListener(name, (ev) => {
        ev.preventDefault();
        resourceZone.classList.add("is-over");
      }));
      ["dragleave", "dragend"].forEach((name) => resourceZone.addEventListener(name, () => (
        resourceZone.classList.remove("is-over")
      )));
      resourceZone.addEventListener("drop", (ev) => {
        ev.preventDefault();
        resourceZone.classList.remove("is-over");
        uploadResourceFiles(ev.dataTransfer?.files);
      });
    }
    const resourceList = $("#resource-list");
    if (resourceList) resourceList.addEventListener("click", (ev) => {
      const del = ev.target.closest("button[data-del]");
      if (del) {
        ev.stopPropagation();
        deleteResource(del.dataset.del);
        return;
      }
      const card = ev.target.closest(".resource-card");
      if (card) toggleResource(card.dataset.name);
    });

    const ytVideoZone = $("#yt-video-drop");
    const ytVideoInput = $("#yt-video-input");
    const ytUploadOpts = { selection: "yt", progress: "#yt-video-progress" };
    if (ytVideoZone && ytVideoInput) {
      ytVideoZone.addEventListener("click", () => ytVideoInput.click());
      ytVideoZone.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          ytVideoInput.click();
        }
      });
      ytVideoInput.addEventListener("change", () => {
        uploadResourceFiles(ytVideoInput.files, ytUploadOpts);
        ytVideoInput.value = "";
      });
      ["dragenter", "dragover"].forEach((name) => ytVideoZone.addEventListener(name, (ev) => {
        ev.preventDefault();
        ytVideoZone.classList.add("is-over");
      }));
      ["dragleave", "dragend"].forEach((name) => ytVideoZone.addEventListener(name, () => (
        ytVideoZone.classList.remove("is-over")
      )));
      ytVideoZone.addEventListener("drop", (ev) => {
        ev.preventDefault();
        ytVideoZone.classList.remove("is-over");
        uploadResourceFiles(ev.dataTransfer?.files, ytUploadOpts);
      });
    }
    const ytVideoList = $("#yt-video-list");
    if (ytVideoList) ytVideoList.addEventListener("click", (ev) => {
      const del = ev.target.closest("button[data-del]");
      if (del) {
        ev.stopPropagation();
        deleteResource(del.dataset.del);
        return;
      }
      const card = ev.target.closest(".resource-card");
      if (card) toggleYtVideo(card.dataset.name);
    });

    const scriptInput = $("#script-input");
    if (scriptInput) scriptInput.addEventListener("input", () => {
      updateScriptMeta();
      scheduleScriptSave(scriptInput.value);
    });
    const clearScript = $("#btn-clear-script");
    if (clearScript) clearScript.addEventListener("click", () => {
      if (scriptInput) scriptInput.value = "";
      try { localStorage.removeItem("manualScript"); } catch (_) { /* noop */ }
      updateScriptMeta();
      toast("Guion manual borrado");
    });
    const gtaSearchBtn = $("#btn-gta-search");
    if (gtaSearchBtn) gtaSearchBtn.addEventListener("click", gtaSearch);
    const gtaTestBtn = $("#btn-gta-test");
    if (gtaTestBtn) gtaTestBtn.addEventListener("click", async () => {
      const status = $("#gta-status");
      if (status) status.textContent = "Probando conexiones...";
      try {
        const r = await api("/api/gta/test", { method: "POST", body: {} });
        const res = r.results || {};
        const lines = Object.keys(res).map((k) => `${k}: ${res[k].ok ? "OK" : (res[k].error || "fallo")}`);
        if (status) status.textContent = lines.join(" · ");
      } catch (e) { if (status) status.textContent = "Error: " + e.message; }
    });
    const gtaTabs = $("#gta-tabs");
    if (gtaTabs) gtaTabs.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".tab[data-tab]");
      if (!btn) return;
      const tab = btn.dataset.tab;
      $$("#gta-tabs .tab").forEach((b) => b.classList.toggle("is-active", b === btn));
      const panels = btn.closest(".card").querySelectorAll(".settings-panel");
      panels.forEach((p) => p.classList.toggle("is-active", p.dataset.panel === tab));
    });
    const gtaGenBtn = $("#btn-gta-generate");
    if (gtaGenBtn) gtaGenBtn.addEventListener("click", async () => {
      const q = ($("#gta-query") || {}).value || "GTA 6";
      const status = $("#gta-status");
      if (status) status.textContent = "Generando short desde noticias...";
      try {
        await api("/api/run", { method: "POST", body: { count: 1, topic: q, seconds: 90 } });
        toast("Generando short desde noticias de GTA Media", "info");
        if (status) status.textContent = "Generación iniciada. Revisa la pestaña Crear.";
      } catch (e) { if (status) status.textContent = "Error: " + e.message; }
    });
    const autoBtn = $("#btn-auto");
    if (autoBtn) autoBtn.addEventListener("click", toggleAutoMode);
    const saveKeysBtn = $("#btn-save-keys");
    if (saveKeysBtn) saveKeysBtn.addEventListener("click", saveApiKeys);
    const saveScriptBtn = $("#btn-save-script-provider");
    if (saveScriptBtn) saveScriptBtn.addEventListener("click", async () => {
      const sel = $("#script-provider");
      const hint = $("#script-provider-hint");
      if (!sel) return;
      try {
        await api("/api/settings/script", { method: "POST", body: { provider: sel.value } });
        const lbl = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : sel.value;
        if (hint) hint.textContent = "Guardado: " + lbl;
        await refresh();
      } catch (err) {
        if (hint) hint.textContent = "Error: " + err.message;
      }
    });
    $("#btn-cancel").addEventListener("click", async () => {
      askConfirm("¿Cancelar la generación?", "Se detiene el proceso actual. Lo que ya estaba hecho se descarta.", async () => {
        try {
          await api("/api/cancel", { method: "POST" });
          toast("Cancelado", "info");
        } catch (err) { toast(err.message, "bad"); }
      });
    });
    $("#btn-again").addEventListener("click", () => {
      state.dismissed = state.data.job.finished;
      setTab("create");
      setJobView({ ...state.data.job, ok: null });
    });
    $("#btn-go-review").addEventListener("click", () => setTab("review"));
    $("#btn-retry").addEventListener("click", () => {
      state.dismissed = state.data.job.finished;
      startRun();
    });

    $("#queue-seg").addEventListener("click", (ev) => {
      const btn = ev.target.closest("button[data-value]");
      if (!btn) return;
      state.queueTab = btn.dataset.value;
      renderQueue();
    });

    $("#btn-approve-all").addEventListener("click", async () => {
      try {
        const res = await api("/api/approve", { method: "POST", body: { id: "all" } });
        toast(`${res.approved.length} short(s) aprobado(s)`);
        await refresh();
      } catch (err) { toast(err.message, "bad"); }
    });

    $("#btn-publish").addEventListener("click", () => publish(false));
    $("#btn-publish-dry").addEventListener("click", () => publish(true));
    // Tema ahora es un input libre (#topic-input)
    const zSave = $("#btn-zernio-save");
    if (zSave) zSave.addEventListener("click", async () => {
      const val = ($("#zernio-key-input") || {}).value || "";
      if (!val.trim()) { const h = $("#zernio-hint"); if (h) h.textContent = "Pega tu API key primero."; return; }
      const h = $("#zernio-hint");
      try {
        await api("/api/zernio/key", { method: "POST", body: { api_key: val.trim() } });
        if (h) h.textContent = "API key guardada.";
        const inp = $("#zernio-key-input");
        if (inp) inp.value = "";
        await refresh();
      } catch (e) { if (h) h.textContent = "Error: " + e.message; }
    });
    const zTest = $("#btn-zernio-test");
    if (zTest) zTest.addEventListener("click", async () => {
      const h = $("#zernio-hint");
      if (h) h.textContent = "Probando...";
      try {
        const r = await api("/api/zernio/test", { method: "POST", body: {} });
        if (r.ok) { if (h) h.textContent = "Conexion OK - " + (r.accounts || []).length + " cuentas."; await refresh(); }
        else if (h) h.textContent = "Fallo: " + (r.error || "sin detalles");
      } catch (e) { if (h) h.textContent = "Error: " + e.message; }
    });
    const settingsTabs = $("#settings-tabs");
    if (settingsTabs) settingsTabs.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".tab[data-tab]");
      if (!btn) return;
      const tab = btn.dataset.tab;
      const view = btn.closest("#view-settings") || document;
      view.querySelectorAll(".settings-tabs .tab").forEach((b) => b.classList.toggle("is-active", b === btn));
      view.querySelectorAll(".settings-panel").forEach((p) => p.classList.toggle("is-active", p.dataset.panel === tab));
    });

    $("#queue-grid").addEventListener("click", (ev) => {
      const open = ev.target.closest("[data-open]");
      if (open) { openModal(open.dataset.open); return; }
      const folder = ev.target.closest("[data-folder]");
      if (folder) {
        const item = findItem(folder.dataset.folder);
        if (item) api("/api/open", { method: "POST", body: { rel: `queue/${item.status}/${item.id}` } })
          .catch((err) => toast(err.message, "bad"));
      }
    });

    $("#modal-approve").addEventListener("click", () => state.modalItem && approve(state.modalItem.id));
    $("#modal-reject").addEventListener("click", () => state.modalItem && reject(state.modalItem.id));
    $("#modal-delete").addEventListener("click", () => state.modalItem && deleteShort(state.modalItem.id));
    $("#modal-folder").addEventListener("click", () => {
      const rel = $("#modal-folder").dataset.rel;
      api("/api/open", { method: "POST", body: { rel } }).catch((err) => toast(err.message, "bad"));
    });
    $$("[data-close]").forEach((el) => el.addEventListener("click", () => {
      if (!$("#confirm").classList.contains("hidden")) {
        $("#confirm").classList.add("hidden");
      } else {
        closeModal();
      }
    }));

    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        if (!$("#confirm").classList.contains("hidden")) $("#confirm").classList.add("hidden");
        else if (!$("#modal").classList.contains("hidden")) closeModal();
      }
    });

    // ------------------------------------------------------------- voz
    const vp = $("#voice-provider");
    if (vp) vp.addEventListener("change", async () => {
      try {
        await api("/api/settings/voice", { method: "POST", body: { provider: vp.value, edge_voice: ($("#edge-voice") || {}).value || "" } });
        toast("Motor de voz actualizado");
        await refresh();
      } catch (e) { toast(e.message, "bad"); }
    });
    const ev = $("#edge-voice");
    if (ev) ev.addEventListener("change", async () => {
      try {
        await api("/api/settings/voice", { method: "POST", body: { provider: vp.value, edge_voice: ev.value } });
        toast("Voz Edge actualizada");
        await refresh();
      } catch (e) { toast(e.message, "bad"); }
    });
    const bgSource = $("#bg-source");
    if (bgSource) bgSource.addEventListener("change", async () => {
      try {
        await api("/api/settings/media", { method: "POST", body: { source: bgSource.value } });
        toast("Fondos actualizados");
        await refresh();
      } catch (e) { toast(e.message, "bad"); }
    });

    // YouTube Clips
    const ytBtn = $("#btn-yt-generate");
    if (ytBtn) ytBtn.addEventListener("click", async () => {
      const url = ($("#yt-url") || {}).value || "";
      const seg = $("#yt-count-seg");
      const count = seg ? Number(seg.dataset.value || 1) : 1;
      const durSeg = $("#yt-dur-seg");
      const duration = durSeg ? Number(durSeg.dataset.value || 60) : 60;
      const subsSeg = $("#yt-subs-seg");
      const subtitles = subsSeg ? subsSeg.dataset.value === "1" : true;
      const resource = selectedYtVideo();
      if (!url.trim() && !resource) { toast("Pega un enlace o elige un vídeo subido", "bad"); return; }
      try {
        await api("/api/youtube-clips", {
          method: "POST",
          body: { url: url.trim(), resource, count, duration, subtitles },
        });
        toast(resource ? "Generando clips desde tu vídeo…" : "Generando clips…");
        pollYtClips();
      } catch (e) { toast(e.message, "bad"); }
    });
    // segmented count
    const ytSeg = $("#yt-count-seg");
    if (ytSeg) ytSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      ytSeg.querySelectorAll("button").forEach((x) => x.classList.remove("is-active"));
      b.classList.add("is-active");
      ytSeg.dataset.value = b.dataset.value;
    }));
    const ytDurSeg = $("#yt-dur-seg");
    if (ytDurSeg) ytDurSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      ytDurSeg.querySelectorAll("button").forEach((x) => x.classList.remove("is-active"));
      b.classList.add("is-active");
      ytDurSeg.dataset.value = b.dataset.value;
    }));
    const ytSubsSeg = $("#yt-subs-seg");
    if (ytSubsSeg) ytSubsSeg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
      ytSubsSeg.querySelectorAll("button").forEach((x) => x.classList.remove("is-active"));
      b.classList.add("is-active");
      ytSubsSeg.dataset.value = b.dataset.value;
    }));
    const voiceTone = $("#voice-tone");
    if (voiceTone) voiceTone.addEventListener("change", async () => {
      try {
        await api("/api/settings/voice-tone", { method: "POST", body: { tone: voiceTone.value } });
        toast("Tono de voz actualizado");
        await refresh();
      } catch (e) { toast(e.message, "bad"); }
    });
    const musicVol = $("#music-volume");
    const musicVal = $("#music-volume-val");
    const musicHint = $("#music-hint");
    const introDb = $("#music-intro-db");
    const fadeIn = $("#music-fade-in");
    const introFade = $("#music-intro-fade");
    const fmtDb = (v) => (Number(v) >= 0 ? "+" : "") + Number(v) + " dB";
    const fmtSec = (v) => Number(v).toFixed(2) + " s";
    const paintMusic = () => {
      if (musicVal && musicVol) musicVal.textContent = fmtDb(musicVol.value);
      const dl = $("#music-intro-db-val");
      if (dl && introDb) dl.textContent = fmtDb(introDb.value);
      const fil = $("#music-fade-in-val");
      if (fil && fadeIn) fil.textContent = fmtSec(fadeIn.value);
      const fol = $("#music-intro-fade-val");
      if (fol && introFade) fol.textContent = fmtSec(introFade.value);
    };
    const saveMusic = async () => {
      if (!musicVol) return;
      const body = {
        music_db: Number(musicVol.value),
        music_intro_db: Number(introDb ? introDb.value : 1.5),
        music_intro_fade: Number(introFade ? introFade.value : 0.7),
        music_fade_in: Number(fadeIn ? fadeIn.value : 0.3),
      };
      try {
        await api("/api/settings/music", { method: "POST", body });
        paintMusic();
        if (musicHint) {
          musicHint.textContent = "Guardado: " + fmtDb(body.music_db)
            + " · entrada " + fmtDb(body.music_intro_db)
            + " · fundido " + fmtSec(body.music_fade_in)
            + " · bajada " + fmtSec(body.music_intro_fade) + ".";
        }
        toast("Ajustes de música actualizados");
        await refresh();
      } catch (e) { toast(e.message, "bad"); }
    };
    [musicVol, introDb, fadeIn, introFade].forEach((el) => {
      if (!el) return;
      el.addEventListener("input", paintMusic);
      el.addEventListener("change", saveMusic);
    });
    const stepMusic = (delta) => {
      if (!musicVol) return;
      const next = Math.max(Number(musicVol.min), Math.min(Number(musicVol.max), Number(musicVol.value) + delta));
      musicVol.value = next;
      paintMusic();
      saveMusic();
    };
    const musicMinus = $("#music-minus");
    if (musicMinus) musicMinus.addEventListener("click", () => stepMusic(-1));
    const musicPlus = $("#music-plus");
    if (musicPlus) musicPlus.addEventListener("click", () => stepMusic(1));
    const musicSave = $("#btn-music-save");
    if (musicSave) musicSave.addEventListener("click", saveMusic);
    const frameGrid = $("#frame-grid");
    if (frameGrid) frameGrid.addEventListener("click", async (ev) => {
      const card = ev.target.closest(".frame-card");
      if (!card || card.disabled) return;
      const key = card.dataset.frame || "";
      try {
        await api("/api/settings/frame", { method: "POST", body: { frame_template: key } });
        await refresh();
        toast(key ? "Marco activado para el próximo vídeo" : "Marco desactivado");
      } catch (e) { toast(e.message, "bad"); }
    });
    const vi = $("#voice-intensity");
    if (vi) {
      vi.addEventListener("input", () => {
        const v = $("#voice-intensity-val");
        if (v) v.textContent = (vi.value >= 0 ? "+" : "") + vi.value + "%";
      });
      vi.addEventListener("change", async () => {
        try {
          await api("/api/settings/voice-intensity", { method: "POST", body: { intensity: Number(vi.value) } });
          toast("Intensidad de voz actualizada");
          await refresh();
        } catch (e) { toast(e.message, "bad"); }
      });
    }
    const omOpen = $("#btn-openmontage-open");
    if (omOpen) omOpen.addEventListener("click", () => {
      window.open("https://github.com/calesthio/OpenMontage", "_blank");
    });
    const omSetup = $("#btn-openmontage-setup");
    if (omSetup) omSetup.addEventListener("click", () => {
      const h = $("#openmontage-hint");
      if (h) h.textContent = "Clona el repo y sigue su README (Python 3.10+, Node 18+, FFmpeg). Luego ejecútalo con tu asistente de IA.";
    });

    const zone = $("#dropzone");
    const input = $("#voice-input");
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); input.click(); }
    });
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) uploadVoice(input.files[0]);
      input.value = "";
    });
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (ev) => {
      ev.preventDefault();
      zone.classList.add("is-over");
    }));
    ["dragleave", "dragend"].forEach((name) => zone.addEventListener(name, () => zone.classList.remove("is-over")));
    zone.addEventListener("drop", (ev) => {
      ev.preventDefault();
      zone.classList.remove("is-over");
      const file = ev.dataTransfer?.files?.[0];
      if (file) uploadVoice(file);
    });

    $("#voice-play").addEventListener("click", () => {
      const audio = $("#voice-audio");
      if (state.audioPlaying) {
        audio.pause();
      } else {
        audio.play().catch(() => toast("No puedo reproducir esa referencia", "bad"));
      }
    });
    $("#voice-audio").addEventListener("play", () => {
      state.audioPlaying = true;
      $("#voice-play").innerHTML = icon("pause");
    });
    $("#voice-audio").addEventListener("pause", () => {
      state.audioPlaying = false;
      $("#voice-play").innerHTML = icon("play");
    });

    // --------------------------------------------------------- ajustes
    document.addEventListener("click", (ev) => {
      const open = ev.target.closest("[data-open-dir]");
      if (open) {
        api("/api/open", { method: "POST", body: { rel: open.dataset.openDir } })
          .catch((err) => toast(err.message, "bad"));
      }
    });

    $("#btn-shutdown").addEventListener("click", () => {
      askConfirm("¿Cerrar ANGEL PRODUCE IA?", "Se apaga el servidor local. Puedes volver a abrirlo con el acceso directo.", async () => {
        try { await api("/api/shutdown", { method: "POST" }); } catch (_) { /* se apaga igual */ }
        document.body.innerHTML = `<div style="min-height:100vh;display:grid;place-items:center;text-align:center;color:#98a1b3;font-family:system-ui">
          <div><h1 style="color:#eef0f6;font-size:22px;margin:0 0 8px">ANGEL PRODUCE IA cerrado</h1>
          <p style="margin:0">Puedes cerrar esta pestaña.</p></div></div>`;
      }, false);
    });
  }

  /* ------------------------------------------------------------- inicio */

  async function boot() {
    bind();
    restoreCreateExtras();
    try {
      state.data = await api("/api/state");
      pruneResourceSelection();
      pruneYtVideo();
      renderAll();
      setJobView(state.data.job);
      try {
        const ap = await api("/api/autopilot");
        if (ap && ap.config) {
          state.autoMode = !!ap.config.enabled;
          localStorage.setItem("autoMode", state.autoMode ? "1" : "0");
        }
      } catch (_) { /* noop */ }
      updateAutoButton();
      if (state.autoMode) autoStart();
      if (!state.data.voice.exists) {
        toast("Sube tu voz de referencia para empezar", "info");
      }
    } catch (err) {
      toast(`No encuentro el servidor local: ${err.message}`, "bad");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    boot();
    if (window.anime) {
      try { anime({ targets: ".brand-mark", scale: [0.6, 1], opacity: [0, 1], duration: 900, easing: "easeOutElastic(1, .5)" }); } catch (e) {}
    }
  });
})();
