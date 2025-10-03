#!/bin/bash
set -e

echo ">>> Atualizando pacotes..."
sudo apt update

echo ">>> Instalando dependências do USBUtil..."
sudo apt install -y \
    python3 \
    python3-pyqt5 \
    python3-pyqt5.qtsvg \
    python3-pyqt5.qtquick \
    gcc \
    make \
    dosfstools \
    util-linux \
    udisks2 \
    python3-pip \
    python3-venv

echo ">>> Todas as dependências foram instaladas com sucesso!"
echo "Agora você pode rodar o programa com: python3 USButil.py"
