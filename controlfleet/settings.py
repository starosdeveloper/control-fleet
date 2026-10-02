"""
Настройки проекта «Контроль грузового парка».

Секреты и окружение читаются из .env (ТЗ §10: настройки и секреты вне кода).
Движок БД переключается через DB_ENGINE: sqlite для локальной разработки,
postgres для рабочего сервера (ТЗ §2).
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Базовое ------------------------------------------------------------------

DEBUG = _env_bool("DJANGO_DEBUG", False)

# На проде ключ обязан прийти из окружения. Локально при отсутствии — временный,
# чтобы `manage.py` не падал; такой ключ не переживает перезапуск, что и нужно в dev.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or ""
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError(
            "DJANGO_SECRET_KEY не задан, а DEBUG выключен. "
            "На рабочем сервере ключ обязателен (ТЗ §10)."
        )
    SECRET_KEY = "dev-insecure-" + secrets.token_urlsafe(32)

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "fleet",
    "drivers",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "controlfleet.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "controlfleet.wsgi.application"

# --- База данных --------------------------------------------------------------

if os.environ.get("DB_ENGINE", "sqlite").strip().lower() == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "controlfleet"),
            "USER": os.environ.get("DB_USER", "controlfleet"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# --- Пароли -------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Локаль и время -----------------------------------------------------------

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"  # ТЗ §3
USE_I18N = True
USE_TZ = True

# --- Статика и файлы ----------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Закрытое файловое хранилище (ТЗ §9): вне веб-корня, раздача только через проверку прав.
PRIVATE_MEDIA_ROOT = Path(
    os.environ.get("PRIVATE_MEDIA_ROOT") or (BASE_DIR / "private_media")
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Сессии и безопасность ----------------------------------------------------

# Водительская сессия завершается через 24 часа (ТЗ §4).
DRIVER_SESSION_MAX_AGE = 24 * 60 * 60

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"

_SECURE = _env_bool("DJANGO_SECURE_SSL", False)
SESSION_COOKIE_SECURE = _SECURE
CSRF_COOKIE_SECURE = _SECURE
SECURE_SSL_REDIRECT = _SECURE
if _SECURE:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Доменные значения по умолчанию (ТЗ §3, §6) -------------------------------

MAINTENANCE_INTERVAL_KM = 50_000       # интервал ТО по умолчанию
MAINTENANCE_WARNING_KM = 5_000         # жёлтая зона «скоро ТО»
GPS_DRIVER_CONFLICT_KM = 1_000         # расхождение GPS/водителя за одну дату
ODOMETER_DAILY_JUMP_KM = 3_000         # скачок за сутки → на проверку (настраивается)

# Антибрутфорс водительского входа (ТЗ §4).
DRIVER_LOGIN_MAX_ATTEMPTS = 5
DRIVER_LOGIN_WINDOW_SECONDS = 15 * 60

LOGIN_URL = "driver_login"
