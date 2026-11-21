"""Interfaz de línea de comandos."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, doctor as doctor_mod, pipeline, queue, script_gen
from . import autopilot as autopilot_mod, channels as channels_mod, youtube as youtube_mod
from .config import get, load_config, path_of
from .util import (APPROVED, IMAGE_EXT, PENDING, PUBLISHED, VIDEO_EXT, err,
                   ensure_dirs, fmt_dur, info, ok, setup_console, shorten, step)

BANNER = """
  ANGEL PRODUCE IA
  Estudio de vídeo con tu voz clonada
  ─────────────────────────────────────
"""


def _print_queue(status: str) -> None:
    items = queue.list_shorts(status)
    if not items:
        info(f"({status}) vacío")
        return
    for meta in items:
        title = shorten(meta.get("title", "sin título"), 58)
        duration = fmt_dur(float(meta.get("duration") or 0))
        platforms = ",".join(meta.get("published") or {}) or "-"
        print(f"  {meta['id']:<44} {duration:>5}  {title}  [{platforms}]")


def cmd_run(args) -> int:
    print(BANNER)
    cfg = load_config()
    manual = None
    script_file = getattr(args, "script_file", None)
    if script_file:
        path = Path(script_file).expanduser()
        if not path.is_file():
            err(f"No encuentro el archivo del guion: {path}")
            return 2
        manual = path.read_text(encoding="utf-8-sig", errors="replace")
        if not manual.strip():
            err("El archivo del guion está vacío.")
            return 2
    resources: list[Path] = []
    for raw in (getattr(args, "resource", None) or []):
        path = Path(raw).expanduser()
        if not path.is_file():
            err(f"No encuentro el recurso: {path}")
            return 2
        if path.suffix.lower() not in IMAGE_EXT | VIDEO_EXT:
            err(f"«{path.name}» no es una imagen o vídeo compatible.")
            return 2
        resources.append(path)
    folders = pipeline.generate_many(
        cfg, args.count, args.topic, getattr(args, "duration", None),
        sources=getattr(args, "sources", None) or None,
        manual_script=manual,
        resources=resources or None,
    )
    print()
    for folder in folders:
        ok(str(folder))
    info("Revísalos en la interfaz y apruébalos para publicar.")
    return 0


def cmd_doctor(_args) -> int:
    return doctor_mod.main()


def cmd_list(args) -> int:
    _print_queue(args.status)
    return 0


def cmd_approve(args) -> int:
    status = "all" if args.id == "all" else None
    if status == "all":
        for meta in queue.list_shorts("pending"):
            queue.approve_short(meta["id"])
            ok(f"Aprobado: {meta['id']}")
        return 0
    queue.approve_short(args.id)
    ok(f"Aprobado: {args.id}")
    return 0


def cmd_reject(args) -> int:
    queue.reject_short(args.id)
    ok(f"Descartado: {args.id}")
    return 0


def cmd_delete(args) -> int:
    if queue.delete_short(args.id):
        ok(f"Eliminado: {args.id}")
    else:
        err("No existe ese short.")
    return 0


def cmd_publish(args) -> int:
    from . import publish as publish_mod

    cfg = load_config()
    results = publish_mod.publish_approved(cfg, dry_run=args.dry_run)
    if not results:
        info("No hay nada aprobado.")
        return 0
    for short_id, platforms in results.items():
        for platform, result in platforms.items():
            if result.get("ok"):
                ok(f"{short_id} → {platform} {result.get('url') or result.get('id') or ''}")
            else:
                err(f"{short_id} → {platform}: {result.get('error')}")
    return 0


def cmd_topics(_args) -> int:
    cfg = load_config()
    topics = script_gen.topic_list(cfg)
    print(f"  {len(topics)} temas · rotación automática, sin límite de vídeos")
    for item in topics:
        print(f"  {item['id']:<28} {item['title']}")
    return 0


def cmd_voice(_args) -> int:
    cfg = load_config()
    ref = path_of(get(cfg, "voice.ref_audio", "assets/voice/reference.wav"))
    if not ref.exists():
        err("No hay voz de referencia. Súbela desde la interfaz (pestaña «Mi voz»).")
        return 1
    from .util import probe_duration

    ok(f"Voz lista: {ref} · {probe_duration(ref):.1f} s")
    return 0


def cmd_templates(args) -> int:
    from . import graphics

    action = (args.action or "list").lower()
    if action == "add":
        if not args.names:
            err("Dime qué plantilla instalar: run.cmd plantillas add aurora")
            return 1
        for name in args.names:
            try:
                installed = graphics.install(name)
            except Exception as exc:
                err(f"{name}: {exc}")
                continue
            ok(f"Plantilla instalada: {installed}")
        info("Se usará la próxima vez que generes un vídeo (la primera vez tarda ~1 min).")
        return 0

    items = graphics.templates()
    status = graphics.status()
    print(f"  HyperFrames: {'listo' if status['available'] else 'no disponible'}"
          f" · {len(items)} plantillas instaladas")
    for item in items:
        title = item.get("title") or item.get("id")
        tags = ", ".join(item.get("tags") or [])
        print(f"  {item['id']:<20} {title}" + (f"  [{tags}]" if tags else ""))
    print()
    info("Instala más: run.cmd plantillas add aurora plasma mesh-gradient")
    info("Ideas: " + ", ".join(graphics.SUGGESTED))
    return 0


def cmd_channels(args) -> int:
    action = (args.action or "list").lower()
    if action in {"remove", "verify"} and not args.id:
        args.id = args.platform
    if action == "add":
        ch = channels_mod.add_channel(
            args.platform, args.name, args.account or "", args.token or "",
        )
        ok(f"Canal añadido: {ch['id']} · {ch['name']} ({ch['platform']})")
        return 0
    if action == "remove":
        if channels_mod.remove_channel(args.id):
            ok(f"Canal eliminado: {args.id}")
        else:
            err("No existe ese canal.")
        return 0
    if action == "verify":
        result = channels_mod.verify_youtube(args.id)
        if result.get("ok"):
            ok(f"Verificado: {result.get('name')} ({result.get('id')})")
        else:
            err(result.get("error", "No se pudo verificar."))
        return 0
    for channel in channels_mod.list_channels():
        print(f"  {channel['id']:<14} {channel['platform']:<10} {channel['name']}")
    if not channels_mod.list_channels():
        info("Sin canales. Añade uno con: run.cmd canales add youtube \"Mi canal\" --token TU_TOKEN")
    return 0


def cmd_autopilot(args) -> int:
    action = (args.action or "status").lower()
    if action == "set":
        payload = {}
        if args.enabled is not None:
            payload["enabled"] = bool(args.enabled)
        if args.count is not None:
            payload["count"] = int(args.count)
        if args.duration is not None:
            payload["duration"] = int(args.duration)
        if args.approval:
            payload["approval_mode"] = args.approval
        if args.hours:
            payload["schedule_hours"] = [int(x) for x in args.hours.split(",")]
        cfg = autopilot_mod.save(payload)
        ok("Piloto actualizado.")
        print(f"  activo={cfg['enabled']} · {cfg['daily_count']}/día · "
              f"{cfg['duration']}s · aprobación={cfg['approval_mode']}")
        return 0
    if action == "run":
        result = autopilot_mod.run_once(force=True)
        if result.get("ok"):
            ok(f"Generados: {', '.join(result.get('shorts', []))}")
        else:
            err(result.get("error") or result.get("reason", "Fallo"))
        return 0
    cfg = autopilot_mod.load()
    print(f"  activo={cfg['enabled']} · {cfg['daily_count']}/día · {cfg['duration']}s · "
          f"aprobación={cfg['approval_mode']} · horas={cfg['schedule_hours']}")
    print(f"  ejecuciones registradas: {len(autopilot_mod.runs())}")
    return 0


def cmd_youtube(args) -> int:
    action = (args.action or "title").lower()
    text = " ".join(args.text or [])
    if action == "title":
        print(youtube_mod.suggest_title(text))
    elif action == "description":
        print(youtube_mod.suggest_description(text))
    elif action == "hashtags":
        print(" ".join("#" + h for h in youtube_mod.suggest_hashtags(text)))
    elif action == "keywords":
        for idea in youtube_mod.keyword_ideas(text):
            print(f"  {idea}")
    else:
        err(f"Acción desconocida: {action}")
        return 1
    return 0


def cmd_clean(_args) -> int:
    import shutil

    from .util import CACHE, WORK

    for folder in (WORK, CACHE / "media"):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            ok(f"Borrado {folder.name}")
    return 0
    action = (args.action or "title").lower()
    text = " ".join(args.text or [])
    if action == "title":
        print(youtube_mod.suggest_title(text))
    elif action == "description":
        print(youtube_mod.suggest_description(text))
    elif action == "hashtags":
        print(" ".join("#" + h for h in youtube_mod.suggest_hashtags(text)))
    elif action == "keywords":
        for idea in youtube_mod.keyword_ideas(text):
            print(f"  {idea}")
    else:
        err(f"Acción desconocida: {action}")
        return 1
    return 0
    import shutil

    from .util import CACHE, WORK

    for folder in (WORK, CACHE / "media"):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            ok(f"Borrado {folder.name}")
    return 0


def cmd_ui(args) -> int:
    from . import ui

    argv = ["--port", str(args.port)]
    if args.open:
        argv.append("--open")
    return ui.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shorts", description="Generador local de shorts con tu voz clonada.")
    parser.add_argument("--version", action="version", version=f"ANGEL PRODUCE IA {__version__}")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="genera uno o varios shorts")
    run.add_argument("-n", "--count", type=int, default=1, help="cuántos generar")
    run.add_argument("-t", "--topic", default=None, help="id del tema (opcional)")
    run.add_argument("-d", "--duration", type=float, default=None,
                     help="segundos del vídeo (60-120)")
    run.add_argument("--sources", nargs="*", default=None,
                     help="enlaces web de los que sacar la información (máx. 5)")
    run.add_argument("--script-file", default=None,
                     help="archivo de texto con un guion escrito a mano")
    run.add_argument("--resource", action="append", default=None,
                     help="imagen o vídeo propio para el montaje (repetible)")
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="comprueba que todo funciona")
    doctor.set_defaults(func=cmd_doctor)

    ui_p = sub.add_parser("ui", help="abre la interfaz web")
    ui_p.add_argument("--port", type=int, default=8765)
    ui_p.add_argument("--open", action="store_true", help="abrir el navegador")
    ui_p.set_defaults(func=cmd_ui)

    listing = sub.add_parser("list", help="lista la cola")
    listing.add_argument("status", nargs="?", default="pending",
                         choices=["pending", "approved", "published"])
    listing.set_defaults(func=cmd_list)

    approve = sub.add_parser("approve", help="aprueba un short (o todos)")
    approve.add_argument("id")
    approve.set_defaults(func=cmd_approve)

    reject = sub.add_parser("reject", help="descarta un short pendiente")
    reject.add_argument("id")
    reject.set_defaults(func=cmd_reject)

    delete = sub.add_parser("delete", help="elimina un short (cualquier estado)")
    delete.add_argument("id")
    delete.set_defaults(func=cmd_delete)

    publish = sub.add_parser("publish", help="publica los aprobados")
    publish.add_argument("--dry-run", action="store_true", help="simula sin subir nada")
    publish.set_defaults(func=cmd_publish)

    topics = sub.add_parser("topics", help="lista los temas")
    topics.set_defaults(func=cmd_topics)

    templates = sub.add_parser("plantillas", help="lista o instala plantillas de gráficas")
    templates.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    templates.add_argument("names", nargs="*", help="plantillas a instalar (para add)")
    templates.set_defaults(func=cmd_templates)

    voice = sub.add_parser("voice", help="estado de tu voz de referencia")
    voice.set_defaults(func=cmd_voice)

    clean = sub.add_parser("clean", help="borra archivos temporales")
    clean.set_defaults(func=cmd_clean)

    channels = sub.add_parser("canales", help="administra canales conectados")
    channels.add_argument("action", nargs="?", default="list",
                          choices=["list", "add", "remove", "verify"])
    channels.add_argument("platform", nargs="?", help="plataforma (youtube, instagram, facebook, tiktok)")
    channels.add_argument("name", nargs="?", help="nombre del canal (para add)")
    channels.add_argument("--account", default=None)
    channels.add_argument("--token", default=None)
    channels.add_argument("id", nargs="?", help="id del canal (remove/verify)")
    channels.set_defaults(func=cmd_channels)

    autopilot = sub.add_parser("autopilot", help="piloto automático")
    autopilot.add_argument("action", nargs="?", default="status",
                           choices=["status", "set", "run"])
    autopilot.add_argument("--enabled", type=int, default=None, choices=[0, 1])
    autopilot.add_argument("--count", type=int, default=None)
    autopilot.add_argument("--duration", type=int, default=None)
    autopilot.add_argument("--approval", default=None, choices=["manual", "auto"])
    autopilot.add_argument("--hours", default=None, help="horas separadas por coma, ej. 10,16,20")
    autopilot.set_defaults(func=cmd_autopilot)

    yt = sub.add_parser("youtube", help="sugerencias para YouTube")
    yt.add_argument("action", nargs="?", default="title",
                    choices=["title", "description", "hashtags", "keywords"])
    yt.add_argument("text", nargs="*", help="texto base")
    yt.set_defaults(func=cmd_youtube)

    return parser


def main(argv: list[str] | None = None) -> int:
    setup_console()
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print()
        info("Cancelado.")
        return 130
    except Exception as exc:
        err(f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
