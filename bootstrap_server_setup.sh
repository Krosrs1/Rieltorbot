#!/usr/bin/env bash
set -euo pipefail

# One-command server bootstrap:
# - installs base packages (if apt is available)
# - creates project directory
# - clones or updates repository
# - starts interactive VPS auto-setup script

DEFAULT_REPO_URL="https://github.com/Krosrs1/Rieltorbot.git"
DEFAULT_INSTALL_DIR="/opt/realtor-userbot"

usage() {
  cat <<EOF
Usage:
  bash bootstrap_server_setup.sh [--repo-url URL] [--install-dir PATH]

Options:
  --repo-url      Git repository URL with this project
                  (default: $DEFAULT_REPO_URL)
  --install-dir   Target directory on server
                  (default: $DEFAULT_INSTALL_DIR)
EOF
}

REPO_URL="$DEFAULT_REPO_URL"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if command -v apt-get >/dev/null 2>&1; then
  echo "[1/5] Устанавливаю базовые пакеты (git, python3, venv)..."
  if [[ "${EUID}" -eq 0 ]]; then
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip
  else
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 python3-venv python3-pip
  fi
else
  echo "[1/5] apt-get не найден, пропускаю установку пакетов (ожидается, что git/python уже есть)."
fi

echo "[2/5] Подготавливаю папку проекта: $INSTALL_DIR"
if [[ "${EUID}" -eq 0 ]]; then
  mkdir -p "$INSTALL_DIR"
  chown -R "${SUDO_USER:-root}:${SUDO_USER:-root}" "$INSTALL_DIR"
else
  sudo mkdir -p "$INSTALL_DIR"
  sudo chown -R "$USER:$USER" "$INSTALL_DIR"
fi

echo "[3/5] Клонирую/обновляю репозиторий..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch --all --prune
  git -C "$INSTALL_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "[4/5] Проверяю наличие setup_vps_autostart.sh..."
if [[ ! -f "$INSTALL_DIR/setup_vps_autostart.sh" ]]; then
  echo "Не найден $INSTALL_DIR/setup_vps_autostart.sh"
  echo "Проверьте URL репозитория и содержимое проекта."
  exit 1
fi
chmod +x "$INSTALL_DIR/setup_vps_autostart.sh"

echo "[5/5] Запускаю интерактивную настройку и автозапуск..."
cd "$INSTALL_DIR"
exec bash ./setup_vps_autostart.sh
