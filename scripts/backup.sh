#!/usr/bin/env bash
# Ежедневная копия базы и вложений в отдельное закрытое хранилище (ТЗ §10).
# Хранит BACKUP_KEEP_DAYS копий (по умолчанию 30), старше — удаляет.
#
# Копия атомарна: пишется во временный каталог рядом, в итоговое имя
# переименовывается только после того, как все шаги прошли без ошибки — иначе
# ротация могла бы увидеть и посчитать годной недописанную копию.
set -euo pipefail
cd "$(dirname "$0")/.."

# Читаем .env тем же парсером, что settings.py (python-dotenv) — чтобы скрипт
# не разошёлся с приложением в понимании значений (кавычки, пробелы). Если
# python упадёт, $(...) даст пустую строку, eval её молча проглотит и скрипт
# продолжит на одних умолчаниях — ложный успех. Поэтому читаем в переменную и
# проверяем код возврата явно, прежде чем eval.
ENV_SH="$(.venv/bin/python -c "
from dotenv import dotenv_values
import shlex
for k, v in dotenv_values('.env').items():
    print(k + '=' + shlex.quote(v or ''))
")" || { echo "backup: не удалось прочитать .env" >&2; exit 1; }
eval "$ENV_SH"

DB_ENGINE="${DB_ENGINE:-sqlite}"
PRIVATE_MEDIA_ROOT="${PRIVATE_MEDIA_ROOT:-$(pwd)/private_media}"
BACKUP_ROOT="${BACKUP_ROOT:-$(cd .. && pwd)/control-fleet-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

STAMP="$(date +%Y-%m-%d_%H%M%S)"
TMP="$BACKUP_ROOT/.tmp_$STAMP"
FINAL="$BACKUP_ROOT/$STAMP"

mkdir -p "$TMP"
echo "backup: start $STAMP -> $FINAL"

# 1) Манифест счётчиков — снимается ПЕРВЫМ, до дампа, это верхняя граница для
#    сверки после восстановления (verify_restore сравнивает не дамп с собой).
.venv/bin/python manage.py backup_manifest --out "$TMP/manifest.json"

# 2) База.
if [ "$DB_ENGINE" = "postgres" ]; then
  PGPASSWORD="${DB_PASSWORD:-}" pg_dump -Fc \
    -h "${DB_HOST:-127.0.0.1}" -p "${DB_PORT:-5432}" \
    -U "${DB_USER:-controlfleet}" "${DB_NAME:-controlfleet}" \
    -f "$TMP/db.dump"
  echo "backup: postgres dump $(du -h "$TMP/db.dump" | cut -f1)"
else
  # .backup — безопасная онлайн-копия sqlite (берёт блокировку чтения, не
  # портится при параллельной записи), не голый cp файла базы на диске.
  sqlite3 db.sqlite3 ".backup '$TMP/db.sqlite3'"
  echo "backup: sqlite .backup $(du -h "$TMP/db.sqlite3" | cut -f1)"
fi

# 3) Вложения. Символическая ссылка даёт архиву фиксированное имя верхнего
#    каталога (private_media) независимо от реального пути на сервере —
#    restore.sh распаковывает по предсказуемому имени, не угадывая его.
ln -s "$PRIVATE_MEDIA_ROOT" "$TMP/private_media"
tar czhf "$TMP/private_media.tar.gz" -C "$TMP" private_media
rm "$TMP/private_media"
echo "backup: media archive $(du -h "$TMP/private_media.tar.gz" | cut -f1)"

mv "$TMP" "$FINAL"
echo "backup: done -> $FINAL"

# 4) Ротация: хранить только KEEP_DAYS последних копий. Сортировка по имени
#    совпадает с хронологией (имя — метка времени), отдельное поле не нужно.
mapfile -t ALL < <(ls -1 "$BACKUP_ROOT" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$' | sort)
COUNT=${#ALL[@]}
if [ "$COUNT" -gt "$KEEP_DAYS" ]; then
  REMOVE=$((COUNT - KEEP_DAYS))
  for i in $(seq 0 $((REMOVE - 1))); do
    rm -rf "${BACKUP_ROOT:?}/${ALL[$i]}"
    echo "backup: pruned ${ALL[$i]}"
  done
fi
echo "backup: retained $(ls -1 "$BACKUP_ROOT" | grep -cE '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}$') copies"
