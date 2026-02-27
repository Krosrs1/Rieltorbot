#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SERVICE_NAME="realtor-userbot"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="$ROOT_DIR/.env"
VENV_DIR="$ROOT_DIR/.venv"
CONFIG_PATH="$ROOT_DIR/config.json"
DATA_DIR="$ROOT_DIR/data"
DB_PATH_DEFAULT="$DATA_DIR/leads.db"
SESSION_DEFAULT="userbot_session"

prompt() {
  local message="$1"
  local default="${2-}"
  local value
  if [[ -n "$default" ]]; then
    read -r -p "$message [$default]: " value
    echo "${value:-$default}"
  else
    read -r -p "$message: " value
    echo "$value"
  fi
}

prompt_secret() {
  local message="$1"
  local value
  read -r -s -p "$message: " value
  echo
  echo "$value"
}

if [[ "${EUID}" -eq 0 ]]; then
  RUN_USER="${SUDO_USER:-root}"
else
  RUN_USER="${USER}"
fi

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 не найден. Установите Python 3.10+ и повторите запуск."
  exit 1
fi

echo "[1/9] Создаю virtualenv..."
if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[2/9] Устанавливаю зависимости..."
python -m pip install --upgrade pip >/dev/null
pip install -r "$ROOT_DIR/requirements.txt"

echo "[3/9] Собираю параметры..."
API_ID="$(prompt "Введите Telegram API_ID")"
while [[ ! "$API_ID" =~ ^[0-9]+$ ]]; do
  API_ID="$(prompt "API_ID должен быть числом. Повторите ввод")"
done

API_HASH="$(prompt_secret "Введите Telegram API_HASH")"
while [[ -z "$API_HASH" ]]; do
  API_HASH="$(prompt_secret "API_HASH не может быть пустым. Повторите ввод")"
done

TARGET_ID="$(prompt "Введите Telegram ID для уведомлений")"
while [[ ! "$TARGET_ID" =~ ^-?[0-9]+$ ]]; do
  TARGET_ID="$(prompt "Telegram ID должен быть числом. Повторите ввод")"
done

SESSION_NAME="$(prompt "Имя Telethon session" "$SESSION_DEFAULT")"
DB_PATH="$DB_PATH_DEFAULT"
mkdir -p "$DATA_DIR"

echo "[4/9] Настраиваю хранилище SQLite автоматически..."
echo "База будет использоваться по пути: $DB_PATH"

echo "[5/9] Записываю .env..."
cat > "$ENV_FILE" <<EOF
API_ID=$API_ID
API_HASH=$API_HASH
SESSION_NAME=$SESSION_NAME
DB_PATH=$DB_PATH
EOF
chmod 600 "$ENV_FILE"

echo "[6/9] Обновляю config.json..."
python - <<PY
import json
from pathlib import Path

config_path = Path(r"$CONFIG_PATH")
config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("notification", {})["target_telegram_id"] = int("$TARGET_ID")
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("config.json обновлен")
PY

echo "[7/9] Проверяю скрипт..."
python -m py_compile "$ROOT_DIR/userbot.py"

echo "[8/9] Создаю systemd сервис..."
SERVICE_CONTENT="[Unit]
Description=Telegram Realtor Userbot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python ${ROOT_DIR}/userbot.py --config ${CONFIG_PATH} --db \\${DB_PATH} --session \\${SESSION_NAME}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target"

if [[ "${EUID}" -eq 0 ]]; then
  printf '%s\n' "$SERVICE_CONTENT" > "$SERVICE_PATH"
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
else
  printf '%s\n' "$SERVICE_CONTENT" | sudo tee "$SERVICE_PATH" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
fi

echo "[9/9] Готово. Проверка статуса:"
if [[ "${EUID}" -eq 0 ]]; then
  systemctl --no-pager --full status "$SERVICE_NAME" || true
else
  sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
fi

echo
echo "Установлено ✅"
echo "Логи: sudo journalctl -u ${SERVICE_NAME} -f"
