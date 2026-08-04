"""
Cerrojo entre procesos para las tareas que se comen la CPU.

Cada canal corre en su propio proceso. Sin coordinacion, tres picos a la vez
lanzan tres transcripciones que se pelean por los mismos nucleos: las tres van
lentas y el buffer sigue creciendo. Con el cerrojo van en fila y cada una usa
la maquina entera, que en CPU es bastante mas rapido en total.

En una maquina con GPU no hace falta y se puede desactivar.
"""

import os
import time
from contextlib import contextmanager
from pathlib import Path

from registro import obtener

if os.name == "nt":
    import msvcrt
else:
    import fcntl


LOG = obtener("bloqueo")


# Un proceso que muere esperando dejaria su marca de prioridad para siempre y
# frenaria a todos los demas. Pasado este tiempo sin refrescarse, se ignora.
PRIORIDAD_MAX_S = 300


def _intentar(f) -> bool:
    try:
        if os.name == "nt":
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _soltar(f):
    try:
        if os.name == "nt":
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def limitado(ruta: Path, limite: int, etiqueta: str = "", aviso_tras: float = 5.0):
    """Ocupa una de varias plazas compartidas entre procesos."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    archivos = [open(ruta.with_name(f"{ruta.name}.{i}"), "a+b")
                for i in range(max(1, int(limite)))]
    adquirido = None
    inicio = time.time()
    avisado = False
    try:
        while adquirido is None:
            for numero, f in enumerate(archivos, 1):
                if _intentar(f):
                    adquirido = (numero, f)
                    break
            if adquirido is None:
                if not avisado and time.time() - inicio > aviso_tras:
                    LOG.info("⏳ ESPERANDO PLAZA%s",
                             f" · TAREA: {etiqueta}" if etiqueta else "")
                    avisado = True
                time.sleep(0.2)
        numero, f = adquirido
        LOG.info("🔓 PLAZA CONSEGUIDA · %d/%d%s", numero, len(archivos),
                 f" · TAREA: {etiqueta}" if etiqueta else "")
        yield numero
    finally:
        if adquirido is not None:
            _soltar(adquirido[1])
        for f in archivos:
            f.close()


def _marca_prioridad(ruta: Path) -> Path:
    return ruta.with_name(ruta.name + ".prioridad")


def _hay_prioridad(ruta: Path) -> bool:
    try:
        return time.time() - _marca_prioridad(ruta).stat().st_mtime < PRIORIDAD_MAX_S
    except OSError:
        return False


@contextmanager
def exclusivo_si(activo: bool, ruta: Path, etiqueta: str = "",
                 prioritario: bool = False):
    """Toma el cerrojo solo si hace falta serializar.

    Con GPU las tareas pesadas no se pelean por los mismos nucleos y ponerlas
    en fila solo las retrasa, asi que quien llama decide.
    """
    if not activo:
        yield
        return
    with exclusivo(ruta, etiqueta, prioritario=prioritario):
        yield


@contextmanager
def exclusivo(ruta: Path, etiqueta: str = "", aviso_tras: float = 5.0,
              prioritario: bool = False):
    """Espera su turno. Bloqueante, sin tiempo maximo: perder el turno seria
    perder el clip, y el buffer aguanta 15 minutos.

    El cerrojo de fichero no forma cola: todos los que esperan lo intentan cada
    medio segundo y gana cualquiera. Con diez canales transcribiendo sin parar,
    una tarea suelta puede no conseguir el turno jamas; paso de verdad con un
    render, que estuvo 16 minutos esperando mientras las transcripciones se
    iban turnando entre ellas.

    Por eso quien va con 'prioritario' deja una marca mientras espera, y los
    demas no intentan tomar el cerrojo hasta que se levante. Un render termina
    un clip; una transcripcion mas solo alarga la cola.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    marca = _marca_prioridad(ruta)
    f = open(ruta, "a+b")
    inicio = time.time()
    avisado = False
    try:
        while True:
            if prioritario:
                # Refrescar en cada vuelta: si este proceso muere, la marca
                # caduca sola y nadie se queda esperando a un fantasma.
                try:
                    marca.touch()
                except OSError:
                    pass
            elif _hay_prioridad(ruta):
                if not avisado and time.time() - inicio > aviso_tras:
                    LOG.info("⏳ CEDIENDO EL TURNO A UNA TAREA PRIORITARIA%s",
                             f" · TAREA: {etiqueta}" if etiqueta else "")
                    avisado = True
                time.sleep(0.5)
                continue
            if _intentar(f):
                break
            if not avisado and time.time() - inicio > aviso_tras:
                LOG.info("⏳ ESPERANDO TURNO DE CPU%s",
                         f" · TAREA: {etiqueta}" if etiqueta else "")
                avisado = True
            time.sleep(0.5)
        # Ya se tiene el turno: retirar la marca cuanto antes para no frenar a
        # los demas durante todo el trabajo.
        if prioritario:
            marca.unlink(missing_ok=True)
        if avisado:
            LOG.info("🔓 TURNO DE CPU CONSEGUIDO\n   ESPERA: %.0fs", time.time() - inicio)
        yield
    finally:
        if prioritario:
            marca.unlink(missing_ok=True)
        _soltar(f)
        f.close()
