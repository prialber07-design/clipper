"""
Galeria web de la bandeja: ver y descargar los clips desde el movil.

Es la pieza que hace que el sistema sirva con el PC apagado. El servidor
renderiza, avisa por ntfy con el enlace, y tu abres, miras y descargas.

    python web.py                      # suelto, en el puerto 8080
    (servidor.py lo arranca solo junto a los vigilantes)

Usuario y clave salen de CLIPPER_WEB_USUARIO / CLIPPER_WEB_CLAVE. Sin clave
definida no arranca: una bandeja abierta en internet es una fuga, no una
comodidad.
"""

import base64
import hmac
import os
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import clipper

LISTOS = clipper.OUT / "LISTOS"


class Handler(SimpleHTTPRequestHandler):
    usuario = ""
    clave = ""

    def _pedir_credenciales(self):
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="clipper"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _autorizado(self) -> bool:
        cabecera = self.headers.get("Authorization", "")
        if not cabecera.startswith("Basic "):
            return False
        try:
            texto = base64.b64decode(cabecera[6:]).decode("utf-8")
            usuario, _, clave = texto.partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        # compare_digest para no filtrar la clave por tiempo de respuesta.
        return (hmac.compare_digest(usuario, self.usuario)
                and hmac.compare_digest(clave, self.clave))

    def do_GET(self):
        if self.path == "/salud":
            cuerpo = b"ok"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
            return
        if not self._autorizado():
            return self._pedir_credenciales()
        super().do_GET()

    def do_HEAD(self):
        if not self._autorizado():
            return self._pedir_credenciales()
        super().do_HEAD()

    def end_headers(self):
        # Que el movil no sirva un clip viejo de cache tras re-renderizar.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, formato, *args):
        pass  # el log del servidor ya es bastante ruidoso


def arrancar(puerto: int = None, en_hilo: bool = False):
    usuario = os.environ.get("CLIPPER_WEB_USUARIO", "clips")
    clave = os.environ.get("CLIPPER_WEB_CLAVE", "")
    if not clave:
        print("[!] Sin CLIPPER_WEB_CLAVE la galeria no arranca "
              "(no se publica la bandeja sin proteger)")
        return None

    puerto = puerto or int(os.environ.get("CLIPPER_WEB_PUERTO", "8080"))
    LISTOS.mkdir(parents=True, exist_ok=True)

    Handler.usuario, Handler.clave = usuario, clave
    handler = partial(Handler, directory=str(LISTOS))
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), handler)
    servidor.daemon_threads = True

    print(f"[>] Galeria en http://0.0.0.0:{puerto}  (usuario: {usuario})")
    if en_hilo:
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        return servidor
    servidor.serve_forever()


if __name__ == "__main__":
    arrancar()
