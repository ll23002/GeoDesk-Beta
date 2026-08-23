#!/bin/bash
# tengo que revisar esto para producción

set -e

SERVICE_NAME="geodesk"
SERVICE_FILE="$(dirname "$(realpath "$0")")/geodesk-celery.service"
SYSTEMD_DIR="/etc/systemd/system"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   GeoDesk-Beta — Instalación de Servicio Systemd    ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Verificar privilegios root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Ejecutar con sudo: sudo bash systemd/install_systemd_service.sh"
    exit 1
fi

# Verificar que Docker esté disponible
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker no está instalado en este servidor."
    exit 1
fi

echo "Docker detectado: $(docker --version)"

# Verificar que el archivo .service exista
if [ ! -f "$SERVICE_FILE" ]; then
    echo "ERROR: No se encontró el archivo de servicio: $SERVICE_FILE"
    exit 1
fi

# Detectar la ruta real del proyecto en el servidor
GEODESK_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
echo "Ruta detectada del proyecto: $GEODESK_DIR"

# Verificar que docker-compose.yml existe en esa ruta
if [ ! -f "$GEODESK_DIR/docker-compose.yml" ]; then
    echo "ERROR: No se encontró docker-compose.yml en $GEODESK_DIR"
    echo "   Asegúrate de ejecutar este script desde el repositorio clonado."
    exit 1
fi

# Actualizar la ruta WorkingDirectory en el .service
TEMP_SERVICE="/tmp/${SERVICE_NAME}.service"
sed "s|WorkingDirectory=.*|WorkingDirectory=${GEODESK_DIR}|g" "$SERVICE_FILE" > "$TEMP_SERVICE"

# Actualizar el usuario si es diferente al actual
CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-geociencias}")"
sed -i "s|^User=.*|User=${CURRENT_USER}|g" "$TEMP_SERVICE"
sed -i "s|^Group=.*|Group=${CURRENT_USER}|g" "$TEMP_SERVICE"

echo "Usuario de ejecución: $CURRENT_USER"

# Instalar el servicio
echo ""
echo "[1/4] Copiando ${SERVICE_NAME}.service a ${SYSTEMD_DIR}..."
cp "$TEMP_SERVICE" "${SYSTEMD_DIR}/${SERVICE_NAME}.service"
chmod 644 "${SYSTEMD_DIR}/${SERVICE_NAME}.service"
rm -f "$TEMP_SERVICE"

echo "[2/4] Recargando Systemd..."
systemctl daemon-reload

echo "[3/4] Habilitando ${SERVICE_NAME} para inicio automático al reiniciar..."
systemctl enable "$SERVICE_NAME"

echo "[4/4] Iniciando el stack de GeoDesk..."
systemctl start "$SERVICE_NAME"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║       Instalación completada correctamente           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Comandos útiles:"
echo "  Estado del servicio:  sudo systemctl status ${SERVICE_NAME}"
echo "  Logs del servicio:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Logs de contenedores: docker compose -f ${GEODESK_DIR}/docker-compose.yml logs -f"
echo "  Estado contenedores:  docker ps"
echo "  Reiniciar stack:      sudo systemctl restart ${SERVICE_NAME}"
echo "  Detener stack:        sudo systemctl stop ${SERVICE_NAME}"
echo ""
