#!/usr/bin/env bash
# Восстановить резервную копию на ТЕСТОВЫЙ стенд и сверить с манифестом (ТЗ §10).
#
# Никогда не трогает рабочую db.sqlite3 / private_media этого дерева — пишет
# только в RESTORE_DIR (по умолчанию ./restore_test, вне PRIVATE_MEDIA_ROOT и
# вне рабочей базы). Восстановление «на том же месте» — отдельное, более
# опасное действие, этот скрипт его не делает.
#
# Использование: scripts/restore.sh [STAMP]
#   STAMP — имя копии (2026-10-03_120000); без аргумента берётся последняя.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_SH="$(.venv/bin/python -c "
from dotenv import dotenv_values
import shlex
for k, v in dotenv_values('.env').items():
    print(k + '=' + shlex.quote(v or ''))
")" || { echo "restore: не удалось прочитать .env" >&2; exit 1; }
eval "$ENV_SH"

BACKUP_ROOT="${BACKUP_ROOT:-$(cd .. && pwd)/control-fleet-backups}"
RESTORE_DIR="${RESTORE_DIR:-$(pwd)/restore_test}"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
  TARGET="$(ls -1 "$BACKUP_ROOT" 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$' | sort | tail -1)"
fi
if [ -z "$TARGET" ]; then
  echo "restore: в $BACKUP_ROOT нет ни одной копии" >&2
  exit 1
fi
SRC="$BACKUP_ROOT/$TARGET"
if [ ! -d "$SRC" ]; then
  echo "restore: копия не найдена: $SRC" >&2
  exit 1
fi
echo "restore: источник $SRC"

rm -rf "$RESTORE_DIR"
mkdir -p "$RESTORE_DIR"

if [ -f "$SRC/db.sqlite3" ]; then
  cp "$SRC/db.sqlite3" "$RESTORE_DIR/db.sqlite3"
  echo "restore: sqlite скопирован -> $RESTORE_DIR/db.sqlite3"
elif [ -f "$SRC/db.dump" ]; then
  echo "restore: найден postgres dump ($SRC/db.dump)."
  echo "restore: автоматическое восстановление в тестовую postgres-базу на этом"
  echo "restore: стенде не настроено (нет согласованного сервера) — создать"
  echo "restore: тестовую БД и выполнить 'pg_restore -d <test_db> $SRC/db.dump'"
  echo "restore: руками на целевом сервере. См. docs/SPEC.md."
  exit 0
else
  echo "restore: не найден ни db.sqlite3, ни db.dump в $SRC" >&2
  exit 1
fi

tar xzf "$SRC/private_media.tar.gz" -C "$RESTORE_DIR"
MEDIA_DIR="$RESTORE_DIR/private_media"
echo "restore: вложения распакованы -> $MEDIA_DIR"

.venv/bin/python manage.py verify_restore \
  --db "$RESTORE_DIR/db.sqlite3" \
  --media "$MEDIA_DIR" \
  --manifest "$SRC/manifest.json"
