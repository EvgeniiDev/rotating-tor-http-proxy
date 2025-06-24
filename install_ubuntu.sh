#!/bin/bash

set -e

PROJECT_DIR="/opt/tor-http-proxy"
SERVICE_NAME="tor-http-proxy"
USER="tor-proxy"
TOR_PROCESSES=50

echo "=== Установка Tor HTTP Proxy на Ubuntu 22.04 ==="

if [[ $EUID -ne 0 ]]; then
   echo "Этот скрипт должен быть запущен от имени root (используйте sudo)" 
   exit 1
fi

echo "Обновление системы..."
apt update && apt upgrade -y

echo "Установка зависимостей..."
apt install -y python3 python3-pip python3-venv tor git

echo "Создание пользователя для сервиса..."
if ! id "$USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$PROJECT_DIR" "$USER"
fi

echo "Создание директории проекта..."
mkdir -p "$PROJECT_DIR"

echo "Копирование файлов проекта..."
cp -r src/* "$PROJECT_DIR/"

echo "Создание виртуального окружения..."
cd "$PROJECT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "Настройка директорий Tor..."
mkdir -p ~/.tor_proxy/config
mkdir -p ~/.tor_proxy/data
mkdir -p ~/.tor_proxy/logs
chmod 755 ~/.tor_proxy/config
chmod 755 ~/.tor_proxy/data
chmod 755 ~/.tor_proxy/logs

echo "Настройка прав доступа..."
chown -R "$USER:$USER" "$PROJECT_DIR"

echo "Создание systemd сервиса..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Tor HTTP Proxy with Load Balancer

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONPATH=$PROJECT_DIR
Environment=TOR_PROCESSES=$TOR_PROCESSES
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/start_new.py
Restart=always
RestartSec=10
MemoryAccounting=yes
MemoryMax=4G

[Install]
WantedBy=multi-user.target
EOF

echo "Перезагрузка systemd и включение сервиса..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME

echo "=== Установка завершена! ==="
echo ""
echo "Управление сервисом:"
echo "  sudo systemctl start $SERVICE_NAME    # Запуск"
echo "  sudo systemctl stop $SERVICE_NAME     # Остановка"
echo "  sudo systemctl restart $SERVICE_NAME  # Перезапуск"
echo "  sudo systemctl status $SERVICE_NAME   # Статус"
echo "  journalctl -u $SERVICE_NAME -f        # Логи"
echo ""
echo "После запуска сервиса:"
echo "  HTTP Proxy: http://localhost:8080"
echo "  Admin Panel: http://localhost:5000"
echo ""
echo "Запустить сейчас? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    systemctl start $SERVICE_NAME
    echo "Сервис запущен!"
    echo "Проверьте статус: sudo systemctl status $SERVICE_NAME"
fi
