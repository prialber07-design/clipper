"""Registro de consola compartido por los procesos del clipper."""

import logging
import sys


class FormatoLegible(logging.Formatter):
    """Formato compacto: una línea por evento y sangría para excepciones."""

    ICONOS = {
        "DEBUG": "🔎",
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🚨",
    }

    def __init__(self):
        super().__init__("%(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z")

    def format(self, record):
        mensaje = super().format(record).replace("\n", "\n    ")
        icono = self.ICONOS.get(record.levelname, "•")
        return (f"{self.formatTime(record, self.datefmt)} | {icono} {record.levelname:<8} | "
                f"{record.name.upper()} | PID={record.process} | {mensaje}")


def obtener(nombre: str) -> logging.Logger:
    """Devuelve un logger con fecha, nivel, proceso y modulo.

    Cada vigilante escribe a su propio stdout, que el supervisor redirige a su
    log de canal. No se escribe a un fichero adicional ni se duplican handlers.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logger = logging.getLogger(f"clipper.{nombre}")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(FormatoLegible())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger
