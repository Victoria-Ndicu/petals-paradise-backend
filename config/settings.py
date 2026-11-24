"""Django settings for Petals Paradise backend.

All environment-specific values come from environment variables so the same
code runs locally, in tests and on Render. No secrets live in source control.
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DEBUG = env_bool("DEBUG", False)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-only-key-do-not-use-in-production"
    else:
        raise RuntimeError("SECRET_KEY environment variable is required when DEBUG is off.")

# Render injects RENDER_EXTERNAL_HOSTNAME automatically.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host:
    ALLOWED_HOSTS.append(_render_host)

CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
if _render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "django_filters",
    "apps.core",
    "apps.accounts",
    "apps.catalogue",
    "apps.orders",
    "apps.inquiries",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
# Render provides DATABASE_URL for its managed Postgres. Falls back to SQLite
# for local development / tests only. Never let a production deployment start
# against an ephemeral local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DEBUG and not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required when DEBUG is off.")

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
        conn_health_checks=True,
        ssl_require=not DEBUG,
    )
}
if not DEBUG and DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    raise RuntimeError("A persistent database is required when DEBUG is off; SQLite is for local development only.")
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
    DATABASES["default"].setdefault("OPTIONS", {})["connect_timeout"] = 10

# --- Cache (used by DRF throttling) ---------------------------------------
# Throttle counters must be shared across gunicorn workers, so per-process
# locmem is not enough in production. Use Redis when REDIS_URL is set,
# otherwise a table in Postgres (created by `manage.py createcachetable`).
if os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.environ["REDIS_URL"],
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
        }
    }

# --- Auth -----------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.PhoneOrEmailBackend"]

# Argon2id first; PBKDF2 stays only so existing hashes (if any) still verify.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Social sign-in: OAuth client IDs the ID token must have been issued for (comma separated).
# Google: the Web client ID(s) from Google Cloud Console. Apple: the Services ID (web)
# and/or app bundle ID (iOS). Leave empty to disable that provider.
GOOGLE_CLIENT_IDS = env_list("GOOGLE_CLIENT_IDS")
APPLE_CLIENT_IDS = env_list("APPLE_CLIENT_IDS")

# --- Email ---------------------------------------------------------------
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173").rstrip("/")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "Petals Paradise <noreply@localhost>")
EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG and not EMAIL_HOST
    else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_VERIFICATION_TIMEOUT_SECONDS = 60 * 60 * 24

# --- Order alerts --------------------------------------------------------
ORDER_ALERT_EMAILS = env_list("ORDER_ALERT_EMAILS")

# --- Sessions / cookies ---------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14  # 14 days
SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")
# The SPA reads the csrftoken cookie and echoes it in X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False

# --- CORS -----------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = [
    "accept",
    "content-type",
    "x-csrftoken",
    "idempotency-key",
    "x-request-id",
]

# --- i18n -----------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = False
USE_TZ = True

# --- Static files ---------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
_storage_config = {
    "bucket_name": os.environ.get("STORAGE_BUCKET", ""),
    "endpoint_url": os.environ.get("STORAGE_ENDPOINT_URL", ""),
    "access_key": os.environ.get("STORAGE_ACCESS_KEY_ID", ""),
    "secret_key": os.environ.get("STORAGE_SECRET_ACCESS_KEY", ""),
    "custom_domain": os.environ.get("STORAGE_PUBLIC_BASE", ""),
}
if any(_storage_config.values()) and not all(_storage_config.values()):
    raise RuntimeError("All STORAGE_* settings are required when configuring object storage.")
if not DEBUG and not all(_storage_config.values()):
    raise RuntimeError("All STORAGE_* settings are required when DEBUG is off.")

STORAGES = {
    "default": (
        {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": _storage_config["bucket_name"],
                "endpoint_url": _storage_config["endpoint_url"],
                "access_key": _storage_config["access_key"],
                "secret_key": _storage_config["secret_key"],
                "region_name": os.environ.get("STORAGE_REGION", "us-east-2"),
                "addressing_style": "path",
                "signature_version": "s3v4",
                "custom_domain": _storage_config["custom_domain"],
                "querystring_auth": False,
                "file_overwrite": False,
                "default_acl": None,
            },
        }
        if all(_storage_config.values())
        else {"BACKEND": "django.core.files.storage.FileSystemStorage"}
    ),
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
SITE_URL = (os.environ.get("SITE_URL") or FRONTEND_URL).rstrip("/")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF ------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "login": os.environ.get("THROTTLE_LOGIN", "10/min"),
        "signup": os.environ.get("THROTTLE_SIGNUP", "10/hour"),
        "inquiry": os.environ.get("THROTTLE_INQUIRY", "5/hour"),
        "order_create": os.environ.get("THROTTLE_ORDER_CREATE", "20/hour"),
    },
}

# --- Business rules -------------------------------------------------------
BUSINESS_TIME_ZONE = "Africa/Nairobi"
CURRENCY = "KES"
# Minimum lead time between "now" and the start of a delivery slot.
MIN_DELIVERY_LEAD_HOURS = int(os.environ.get("MIN_DELIVERY_LEAD_HOURS", "3"))
MAX_DELIVERY_DAYS_AHEAD = int(os.environ.get("MAX_DELIVERY_DAYS_AHEAD", "90"))
# Orders still awaiting payment are cancelled (and reserved stock released) after this long.
# Payments are handled outside this service for now, so staff confirm orders manually:
# the default is a full day. Shorten it once an online payment service is connected.
PAYMENT_TIMEOUT_MINUTES = int(os.environ.get("PAYMENT_TIMEOUT_MINUTES", "1440"))

# --- Production hardening -------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Render health checks hit over plain HTTP internally.
    SECURE_REDIRECT_EXEMPT = [r"^healthz/?$"]

# --- Logging --------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "apps.core.logging.RequestIDFilter"}},
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain", "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
