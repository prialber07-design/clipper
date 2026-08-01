"""
Supervisor: mantiene un vigilante por canal, en Windows o en Linux.

    python servidor.py                 # todos los canales verificados
    python servidor.py --canales a,b   # solo esos
    python servidor.py --estado        # que hay vivo

Reemplaza a vigilar.ps1 para servidor: un unico proceso al que apuntar con
systemd o Docker, que relanza solo cualquier vigilante que se caiga.
"""

import argparse
import os
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import clipper
from registro import obtener

ROOT = clipper.ROOT
DATA = clipper.DATA
OUT = clipper.OUT
LOGS = DATA / "logs"
CONFIG = clipper.CONFIG

REINTENTO_MIN_S = 15
REINTENTO_MAX_S = 300
LOG = obtener("servidor")


class Vigilante:
    def __init__(self, ficha: dict):
        self.canal = ficha["canal"]
        self.plataforma = ficha.get("plataforma", "twitch")
        self.proc = None
        self.espera = REINTENTO_MIN_S
        self.arrancado = None
        self.reinicios = 0
        self.log = None

    def _cerrar_log(self):
        if self.log and not self.log.closed:
            self.log.flush()
            self.log.close()

    def arrancar(self):
        self._cerrar_log()
        LOGS.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(ROOT / "live.py"), "watch", self.canal,
               "--plataforma", self.plataforma]

        entorno = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        extra = {"start_new_session": True} if os.name != "nt" else {}
        self.log = (LOGS / f"{self.canal}.log").open("a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=self.log,
                                     stderr=subprocess.STDOUT, env=entorno, **extra)
        self.arrancado = time.time()
        LOG.info("Vigilante arrancado canal=%s plataforma=%s pid=%s",
                 self.canal, self.plataforma, self.proc.pid)

    def vivo(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def parar(self):
        if not self.vivo():
            self._cerrar_log()
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self._cerrar_log()

    def revisar(self):
        """Relanza si murio. La espera crece si se cae en bucle, para no
        machacar la plataforma ni llenar el log de intentos."""
        if self.vivo():
            # Ha aguantado lo suficiente: la proxima caida no es un bucle.
            if time.time() - self.arrancado > 120:
                self.espera = REINTENTO_MIN_S
            return
        if self.arrancado and time.time() - self.arrancado < self.espera:
            return
        self._cerrar_log()
        if self.proc:
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        self.reinicios += 1
        LOG.warning("Vigilante caído canal=%s; relanzo intento=%d espera_s=%d",
                    self.canal, self.reinicios, self.espera)
        self.arrancar()
        self.espera = min(self.espera * 2, REINTENTO_MAX_S)


def fichas(filtro=None):
    salida = []
    for c in CONFIG.get("canales", []):
        if not c.get("verificado") or not c.get("canal"):
            continue
        if filtro and c["canal"] not in filtro:
            continue
        salida.append(c)
    return salida


def cmd_estado():
    import re
    vivos = []
    if os.name == "nt":
        out = subprocess.run(["wmic", "process", "where", "name='python.exe'",
                              "get", "ProcessId,CommandLine"],
                             capture_output=True, text=True).stdout
    else:
        try:
            out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                                 text=True).stdout
        except FileNotFoundError:
            cmdlines = []
            for p in Path("/proc").glob("[0-9]*/cmdline"):
                try:
                    cmdlines.append(p.read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore"))
                except OSError:
                    pass
            out = "\n".join(cmdlines)
    for linea in out.splitlines():
        m = re.search(r"live\.py\s+watch\s+([A-Za-z0-9_]+)", linea)
        if m:
            vivos.append(m.group(1))
    if not vivos:
        LOG.warning("No hay vigilantes en marcha")
        return 1
    for canal in sorted(set(vivos)):
        listos = sum(clipper.canal_desde_nombre(p.name) == canal
                     for p in (OUT / "LISTOS").glob("*.mp4")) \
            if (OUT / "LISTOS").exists() else 0
        LOG.info("Estado canal=%s vigilante=activo clips_listos=%d (esto no confirma directo)",
                 canal, listos)
    return 0


def limpiar_archivos_antiguos(dias: int = 7):
    """Elimina automáticamente vídeos, transcripciones y logs de /app/clips con más de 7 días de antigüedad."""
    limite_segundos = dias * 86400
    ahora = time.time()
    borrados = 0

    carpetas_a_revisar = [
        OUT / "LISTOS",
        OUT / "REVISAR",
        DATA / "logs",
        DATA / "work"
    ]

    for carpeta in carpetas_a_revisar:
        if not carpeta.exists():
            continue
        for item in list(carpeta.rglob("*")):
            if item.is_file():
                try:
                    mtime = item.stat().st_mtime
                    if (ahora - mtime) > limite_segundos:
                        item.unlink(missing_ok=True)
                        borrados += 1
                        LOG.info("Limpieza: borrado archivo antiguo ruta=%s", item.relative_to(DATA))
                except Exception:
                    pass
            elif item.is_dir():
                try:
                    mtime = item.stat().st_mtime
                    if (ahora - mtime) > limite_segundos and not any(item.iterdir()):
                        shutil.rmtree(item, ignore_errors=True)
                except Exception:
                    pass
    if borrados > 0:
        LOG.info("Limpieza completada borrados=%d antiguedad_dias=%d", borrados, dias)


def main():
    p = argparse.ArgumentParser(description="supervisor de vigilantes")
    p.add_argument("--canales", help="lista separada por comas; por defecto, todos")
    p.add_argument("--estado", action="store_true")
    args = p.parse_args()

    if args.estado:
        sys.exit(cmd_estado())

    filtro = [c.strip() for c in args.canales.split(",")] if args.canales else None
    lista = fichas(filtro)
    if not lista:
        sys.exit("[x] Ningun canal verificado que vigilar")

    LOG.info("Supervisor arrancando vigilantes=%d fecha=%s",
             len(lista), datetime.now().astimezone().isoformat(timespec="seconds"))

    # La galeria va en el mismo proceso: un contenedor, un puerto, una cosa
    # que vigilar.
    try:
        import web
        web.arrancar(en_hilo=True)
    except Exception as e:
        LOG.warning("Galería web no disponible (%s); los clips siguen en %s", e, OUT)

    vigilantes = [Vigilante(f) for f in lista]
    for v in vigilantes:
        v.arrancar()

    def apagar(*_):
        LOG.info("Parando supervisor")
        for v in vigilantes:
            v.parar()
        sys.exit(0)

    signal.signal(signal.SIGINT, apagar)
    signal.signal(signal.SIGTERM, apagar)

    # Limpieza automática inicial al arrancar
    limpiar_archivos_antiguos(dias=7)

    listos_dir = OUT / "LISTOS"
    revisar_dir = OUT / "REVISAR"
    vistos_clips = {p.name for p in listos_dir.glob("*.mp4")} if listos_dir.exists() else set()
    vistos_revisar = {p.name for p in revisar_dir.glob("*.mp4")} if revisar_dir.exists() else set()
    ciclos = 0
    try:
        while True:
            time.sleep(15)
            ciclos += 1
            if ciclos % 240 == 0:  # Cada 1 hora (240 * 15s)
                limpiar_archivos_antiguos(dias=7)

            for v in vigilantes:
                v.revisar()

            # 1. Comprobar si se ha generado un NUEVO CLIP LISTO
            listos_dir = OUT / "LISTOS"
            if listos_dir.exists():
                for mp4 in listos_dir.glob("*.mp4"):
                    if mp4.name not in vistos_clips:
                        vistos_clips.add(mp4.name)
                        partes = mp4.stem.rsplit("-", 2)
                        canal = partes[0] if len(partes) == 3 else "desconocido"
                        LOG.info("Nuevo clip detectado estado=listo canal=%s archivo=%s galeria=disponible",
                                 canal, mp4.name)

            # 2. Comprobar si se ha generado un CLIP EN REVISIÓN
            revisar_dir = OUT / "REVISAR"
            if revisar_dir.exists():
                for mp4 in revisar_dir.glob("*.mp4"):
                    if mp4.name not in vistos_revisar:
                        vistos_revisar.add(mp4.name)
                        partes = mp4.stem.rsplit("-", 2)
                        canal = partes[0] if len(partes) == 3 else "desconocido"
                        LOG.warning("Nuevo clip detectado estado=revisar canal=%s archivo=%s",
                                    canal, mp4.name)

            # 3. Heartbeat del supervisor: confirma procesos, no inventa que hay
            # un directo solo porque quedaron segmentos en el buffer.
            activos = [v.canal for v in vigilantes if v.vivo()]
            LOG.info("Heartbeat supervisor vigilantes_activos=%d/%d canales=%s",
                     len(activos), len(vigilantes), ",".join(activos) or "ninguno")

    except KeyboardInterrupt:
        apagar()


if __name__ == "__main__":
    main()
