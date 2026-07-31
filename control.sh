#!/usr/bin/env bash
# Script de control rápido para la consola de EasyPanel / Docker

case "$1" in
  estado|status)
    python servidor.py --estado
    ;;
  parar|stop)
    echo "[>] Deteniendo vigilantes y servidor..."
    pkill -f "live.py" || true
    pkill -f "servidor.py" || true
    echo "[v] Sistema detenido."
    ;;
  arrancar|start)
    if [ -n "$2" ]; then
      echo "[>] Arrancando canales específicos: $2"
      python servidor.py --canales "$2" &
    else
      echo "[>] Arrancando todos los canales..."
      python servidor.py &
    fi
    echo "[v] Sistema iniciado en segundo plano."
    ;;
  canales)
    echo "[>] Canales verificados en config.json:"
    python -c "import json; cfg=json.load(open('config.json')); print('\n'.join([f'- {c[\"canal\"]} ({c.get(\"plataforma\",\"twitch\")})' for c in cfg.get('canales',[]) if c.get('verificado')]))"
    ;;
  *)
    echo "Comandos disponibles:"
    echo "  ./control.sh estado      -> Muestra qué canales están en vivo y cuántos clips hay"
    echo "  ./control.sh parar       -> Detiene el monitoreo"
    echo "  ./control.sh arrancar    -> Reanuda el monitoreo"
    echo "  ./control.sh canales     -> Lista los canales activos de config.json"
    ;;
esac
