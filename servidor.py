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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class _Tee:
    """Escribe por pantalla y en el log a la vez.

    Bajo pythonw.exe no hay consola donde mirar, y sin esto el supervisor
    trabajaria a ciegas.
    """

    def __init__(self, destino: Path):
        destino.parent.mkdir(parents=True, exist_ok=True)
        self.fichero = destino.open("a", encoding="utf-8", errors="replace")
        self.consola = sys.__stdout__

    def write(self, texto):
        self.fichero.write(texto)
        self.fichero.flush()
        if self.consola:
            try:
                self.consola.write(texto)
            except (OSError, ValueError):
                pass

    def flush(self):
        self.fichero.flush()


def _registrar_salida():
    tee = _Tee(LOGS / "servidor.log")
    sys.stdout = sys.stderr = tee

REINTENTO_MIN_S = 15
REINTENTO_MAX_S = 300


class Vigilante:
    def __init__(self, ficha: dict):
        self.canal = ficha["canal"]
        self.plataforma = ficha.get("plataforma", "twitch")
        self.proc = None
        self.espera = REINTENTO_MIN_S
        self.arrancado = None
        self.reinicios = 0

    def arrancar(self):
        LOGS.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(ROOT / "live.py"), "watch", self.canal,
               "--plataforma", self.plataforma]
        if self.plataforma != "twitch":
            cmd.append("--solo-audio")

        entorno = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        extra = {"start_new_session": True} if os.name != "nt" else {}
        self.log = (LOGS / f"{self.canal}.log").open("a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=self.log,
                                     stderr=subprocess.STDOUT, env=entorno, **extra)
        self.arrancado = time.time()
        print(f"[+] {self.canal:22s} pid {self.proc.pid}")

    def vivo(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def parar(self):
        if not self.vivo():
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            self.proc.kill()

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
        self.reinicios += 1
        print(f"[!] {self.canal} caido, relanzo (intento {self.reinicios}, "
              f"espera {self.espera}s)")
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
        # Bajo la tarea programada el interprete es pythonw.exe, no python.exe:
        # buscar solo uno de los dos hace parecer que no hay nada corriendo.
        out = ""
        for exe in ("python.exe", "pythonw.exe"):
            out += subprocess.run(["wmic", "process", "where", f"name='{exe}'",
                                   "get", "ProcessId,CommandLine"],
                                  capture_output=True, text=True).stdout or ""
    else:
        out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                             text=True).stdout
    for linea in out.splitlines():
        m = re.search(r"live\.py\s+watch\s+([A-Za-z0-9_]+)", linea)
        if m:
            vivos.append(m.group(1))
    if not vivos:
        print("No hay vigilantes en marcha.")
        return
    for canal in sorted(set(vivos)):
        bandeja = Path(os.environ.get("CLIPPER_DATA", ROOT)) / "out" / "LISTOS"
        listos = len(list(bandeja.glob(f"*_{canal}_*.mp4"))) if bandeja.exists() else 0
        print(f"{canal:22s} VIVO   {listos} clips")


def main():
    p = argparse.ArgumentParser(description="supervisor de vigilantes")
    p.add_argument("--canales", help="lista separada por comas; por defecto, todos")
    p.add_argument("--estado", action="store_true")
    args = p.parse_args()

    if args.estado:
        return cmd_estado()

    _registrar_salida()
    filtro = [c.strip() for c in args.canales.split(",")] if args.canales else None
    lista = fichas(filtro)
    if not lista:
        sys.exit("[x] Ningun canal verificado que vigilar")

    print(f"[>] {datetime.now():%Y-%m-%d %H:%M} arrancando {len(lista)} vigilantes")

    # La galeria va en el mismo proceso: un contenedor, un puerto, una cosa
    # que vigilar.
    try:
        import web
        web.arrancar(en_hilo=True)
    except Exception as e:
        print(f"[!] Galeria web no disponible ({e}); los clips siguen en /data")

    vigilantes = [Vigilante(f) for f in lista]
    for v in vigilantes:
        v.arrancar()

    def apagar(*_):
        print("\n[>] Parando todo")
        for v in vigilantes:
            v.parar()
        sys.exit(0)

    signal.signal(signal.SIGINT, apagar)
    signal.signal(signal.SIGTERM, apagar)

    try:
        while True:
            time.sleep(20)
            for v in vigilantes:
                v.revisar()
    except KeyboardInterrupt:
        apagar()


if __name__ == "__main__":
    main()
