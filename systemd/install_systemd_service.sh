#!/bin/bash
#tengo dudas con esto, es buena idea reiniciar todos los servicios de systemd?

set -e  # Detener si cualquier comando falla

# Configuración
SERVICE_NAME="geodesk-celery"
SERVICE_FILE="$(dirname "$0")/${SERVICE_NAME}.service"
SYSTEMD_DIR="/etc/systemd/system"
LOG_DIR="/var/log/geodesk"
RUN_DIR="/run/geodesk"
APP_USER="geociencias"   # tengo que confirmar esto

echo "=== GeoDesk-Beta: Instalando servicio Systemd del Worker Celery ==="
echo ""

# Verificar que se ejecuta como root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Este script debe ejecutarse como root (sudo)."
    exit 1
fi

# Crear directorios necesarios
echo "[1/5] Creando directorios de logs y runtime..."
mkdir -p "$LOG_DIR"
mkdir -p "$RUN_DIR"
chown "$APP_USER:$APP_USER" "$LOG_DIR" "$RUN_DIR"
chmod 755 "$LOG_DIR" "$RUN_DIR"

# Copiar archivo de servicio
echo "[2/5] Copiando ${SERVICE_NAME}.service a ${SYSTEMD_DIR}..."
cp "$SERVICE_FILE" "${SYSTEMD_DIR}/${SERVICE_NAME}.service"
chmod 644 "${SYSTEMD_DIR}/${SERVICE_NAME}.service"

# Recargar Systemd
echo "[3/5] Recargando demonios de Systemd..."
systemctl daemon-reload

# Habilitar el servicio
echo "[4/5] Habilitando ${SERVICE_NAME} para inicio automático..."
systemctl enable "$SERVICE_NAME"

# Iniciar el servicio 
echo "[5/5] Iniciando ${SERVICE_NAME}..."
systemctl start "$SERVICE_NAME"

echo ""
echo "=== Instalación completada ==="
echo ""
echo "Comandos útiles para administrar el servicio:"
echo "  Ver estado:      sudo systemctl status ${SERVICE_NAME}"
echo "  Ver logs:        sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Reiniciar:       sudo systemctl restart ${SERVICE_NAME}"
echo "  Detener:         sudo systemctl stop ${SERVICE_NAME}"
echo "  Ver log archivo: tail -f ${LOG_DIR}/celery-worker.log"
