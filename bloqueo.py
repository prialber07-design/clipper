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


@contextmanager
def exclusivo(ruta: Path, etiqueta: str = "", aviso_tras: float = 5.0):
    """Espera su turno. Bloqueante, sin tiempo maximo: perder el turno seria
    perder el clip, y el buffer aguanta 15 minutos."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    f = open(ruta, "a+b")
    inicio = time.time()
    avisado = False
    try:
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if not avisado and time.time() - inicio > aviso_tras:
                    LOG.info("Esperando turno de CPU%s...",
                             f" para {etiqueta}" if etiqueta else "")
                    avisado = True
                time.sleep(0.5)
        if avisado:
            LOG.info("Turno conseguido tras %.0fs", time.time() - inicio)
        yield
    finally:
        try:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()
