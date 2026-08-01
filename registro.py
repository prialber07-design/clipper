"""Registro de consola compartido por los procesos del clipper."""

import logging
import sys


def obtener(nombre: str) -> logging.Logger:
    """Devuelve un logger con fecha, nivel, proceso y modulo.

    Cada vigilante escribe a su propio stdout, que el supervisor redirige a su
    log de canal. No se escribe a un fichero adicional ni se duplican handlers.
    """
    logger = logging.getLogger(f"clipper.{nombre}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s pid=%(process)d] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        ))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger
