#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env"
VENV_DIR=".venv"
CONFIG_PATH="config.json"
DATA_DIR="data"
DB_PATH_DEFAULT="$DATA_DIR/leads.db"

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

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[1/6] Создаю виртуальное окружение..."
  python3 -m venv "$VENV_DIR"
else
  echo "[1/6] Виртуальное окружение уже существует: $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[2/6] Обновляю pip..."
python -m pip install --upgrade pip >/dev/null

echo "[3/6] Устанавливаю зависимости..."
pip install -r requirements.txt

echo "[4/6] Настройка параметров (вводится один раз)..."
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

SESSION_NAME="$(prompt "Имя сессии Telethon" "userbot_session")"
DB_PATH="$DB_PATH_DEFAULT"
mkdir -p "$DATA_DIR"
echo "SQLite будет использоваться автоматически: $DB_PATH"

cat > "$ENV_FILE" <<EOF
API_ID=$API_ID
API_HASH=$API_HASH
EOF
chmod 600 "$ENV_FILE"

echo "[5/6] Обновляю config.json..."
python - <<PY
import json
from pathlib import Path

config_path = Path("$CONFIG_PATH")
config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("notification", {})["target_telegram_id"] = int("$TARGET_ID")
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("config.json обновлен")
PY

echo "[6/6] Запускаю userbot..."
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

exec python userbot.py --session "$SESSION_NAME" --db "$DB_PATH" --config "$CONFIG_PATH"
