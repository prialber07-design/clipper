"""
Supervisor: mantiene un vigilante por canal, en Windows o en Linux.

    python servidor.py                 # todos los canales verificados
    python servidor.py --canales a,b   # solo esos
    python servidor.py --estado        # que hay vivo

Reemplaza a vigilar.ps1 para servidor: un unico proceso al que apuntar con
systemd o Docker, que relanza solo cualquier vigilante que se caiga.
"""

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
import clipper
import raw
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
        LOG.info("🚀 VIGILANTE INICIADO\n   CANAL: %s\n   PLATAFORMA: %s\n   PID: %s",
                 self.canal, self.plataforma.upper(), self.proc.pid)

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
        LOG.warning("🔁 VIGILANTE CAÍDO\n   CANAL: %s\n   REINICIO: %d\n   PRÓXIMO INTENTO: %ds",
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
        LOG.warning("🔴 SIN VIGILANTES ACTIVOS")
        return 1
    for canal in sorted(set(vivos)):
        listos = sum(clipper.canal_desde_nombre(p.name) == canal
                     for p in (OUT / "LISTOS").glob("*.mp4")) \
            if (OUT / "LISTOS").exists() else 0
        LOG.info("🟢 ESTADO DEL VIGILANTE\n   CANAL: %s\n   ESTADO: ACTIVO\n"
                 "   CLIPS LISTOS: %d\n   NOTA: ESTO NO CONFIRMA QUE HAYA DIRECTO\n"
                 "   vigilante=activo",
                 canal, listos)
    return 0


def _raw_caducado(manifest_path: Path, limite_segundos: float, ahora: float) -> str | None:
    """Devuelve el id del RAW solo si ya cumplio su ciclo y puede borrarse.

    Un RAW pendiente o con error es material que todavia no ha dado clip: si el
    analisis externo va con retraso, borrarlo por antiguedad pierde el candidato
    justo cuando mas falta hace. Solo caduca lo que ya termino en LISTOS o
    REVISAR.
    """
    try:
        if (ahora - manifest_path.stat().st_mtime) <= limite_segundos:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("status") != "completado":
        return None
    try:
        # El id sale de un JSON: validarlo evita construir rutas con '..'.
        return raw.validar_id(manifest.get("id", ""))
    except raw.RawError:
        return None


def limpiar_raw_completados(limite_segundos: float, ahora: float) -> int:
    carpeta = OUT / "RAW"
    if not carpeta.exists():
        return 0
    borrados = 0
    for manifest_path in carpeta.glob("*.json"):
        raw_id = _raw_caducado(manifest_path, limite_segundos, ahora)
        if not raw_id:
            continue
        for ruta in (manifest_path, carpeta / f"{raw_id}.mp4"):
            try:
                if ruta.is_file():
                    ruta.unlink()
                    borrados += 1
                    LOG.info("🧹 LIMPIEZA · RAW COMPLETADO BORRADO\n   RUTA: %s",
                             ruta.relative_to(DATA))
            except OSError as e:
                LOG.warning("⚠️ LIMPIEZA · NO SE PUDO BORRAR\n   RUTA: %s\n   MOTIVO: %s",
                            ruta, e)
    return borrados


def limpiar_archivos_antiguos(dias: int = 7):
    """Elimina automáticamente vídeos, transcripciones y logs de /app/clips con más de 7 días de antigüedad.

    RAW se trata aparte: ahi solo caduca lo ya completado, nunca un candidato
    que sigue esperando analisis.
    """
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
                        LOG.info("🧹 LIMPIEZA · ARCHIVO ANTIGUO BORRADO\n   RUTA: %s",
                                 item.relative_to(DATA))
                except OSError as e:
                    LOG.warning("⚠️ LIMPIEZA · NO SE PUDO BORRAR\n   RUTA: %s\n   MOTIVO: %s",
                                item, e)
            elif item.is_dir():
                try:
                    mtime = item.stat().st_mtime
                    if (ahora - mtime) > limite_segundos and not any(item.iterdir()):
                        shutil.rmtree(item, ignore_errors=True)
                except OSError:
                    pass

    borrados += limpiar_raw_completados(limite_segundos, ahora)

    if borrados > 0:
        LOG.info("✅ LIMPIEZA COMPLETADA\n   ARCHIVOS BORRADOS: %d\n   ANTIGÜEDAD: %d DÍAS",
                 borrados, dias)


def preparar_volumen():
    """Crea el directorio de modelos cuando el bind mount tapa la imagen."""
    try:
        base = DATA.resolve()
    except OSError:
        return
    for variable in ("HF_HOME",):
        valor = os.environ.get(variable, "").strip()
        if not valor:
            continue
        ruta = Path(valor)
        try:
            if not ruta.resolve().is_relative_to(base) or ruta.is_dir():
                continue
            ruta.mkdir(parents=True, exist_ok=True)
            LOG.info("📁 CARPETA DEL VOLUMEN CREADA\n   VARIABLE: %s\n   RUTA: %s",
                     variable, ruta)
        except OSError as e:
            LOG.warning("⚠️ NO SE PUDO CREAR LA CARPETA DEL VOLUMEN\n"
                        "   VARIABLE: %s\n   RUTA: %s\n   MOTIVO: %s",
                        variable, ruta, e)


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

    LOG.info("🚀 SUPERVISOR INICIANDO\n   VIGILANTES: %d\n   FECHA: %s",
             len(lista), datetime.now().astimezone().isoformat(timespec="seconds"))

    preparar_volumen()

    # La galeria va en el mismo proceso: un contenedor, un puerto, una cosa
    # que vigilar.
    try:
        import web
        web.arrancar(en_hilo=True)
    except Exception as e:
        LOG.warning("⚠️ GALERÍA WEB NO DISPONIBLE\n   MOTIVO: %s\n   LOS CLIPS SIGUEN EN: %s", e, OUT)

    vigilantes = [Vigilante(f) for f in lista]
    for v in vigilantes:
        v.arrancar()

    def apagar(*_):
        LOG.info("🛑 SUPERVISOR DETENIENDO")
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

            # Cada 5 minutos, devolver a la cola lo que lleve media hora en
            # 'procesando' sin avanzar. Un hilo puede morir con el proceso
            # vivo, y sin esto su candidato se queda clavado y frena a los
            # demas: paso, y tuvo la cola parada doce horas.
            if ciclos % 20 == 0:
                raw.recuperar_huerfanos(max_edad_s=raw.EDAD_ZOMBI_S)

            raw.procesar_pendientes()

            # 1. Comprobar si se ha generado un NUEVO CLIP LISTO
            listos_dir = OUT / "LISTOS"
            if listos_dir.exists():
                for mp4 in listos_dir.glob("*.mp4"):
                    if mp4.name not in vistos_clips:
                        vistos_clips.add(mp4.name)
                        canal = clipper.canal_desde_nombre(mp4.name)
                        LOG.info("✅ CLIP LISTO\n   CANAL: %s\n   ARCHIVO: %s\n   GALERÍA: DISPONIBLE",
                                 canal, mp4.name)

            # 2. Comprobar si se ha generado un CLIP EN REVISIÓN
            revisar_dir = OUT / "REVISAR"
            if revisar_dir.exists():
                for mp4 in revisar_dir.glob("*.mp4"):
                    if mp4.name not in vistos_revisar:
                        vistos_revisar.add(mp4.name)
                        canal = clipper.canal_desde_nombre(mp4.name)
                        LOG.warning("🟡 CLIP EN REVISIÓN\n   CANAL: %s\n   ARCHIVO: %s",
                                    canal, mp4.name)

            # 3. Heartbeat del supervisor: confirma procesos, no inventa que hay
            # un directo solo porque quedaron segmentos en el buffer.
            activos = [v.canal for v in vigilantes if v.vivo()]
            LOG.info("💓 SUPERVISOR ACTIVO\n   VIGILANTES: %d/%d\n   CANALES: %s",
                     len(activos), len(vigilantes), ", ".join(activos) or "NINGUNO")

    except KeyboardInterrupt:
        apagar()


if __name__ == "__main__":
    main()
