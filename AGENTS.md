# Amnezia Web Panel — AGENTS.md (Antigravity 2.0.11)

Это **lean-роутер**: автозагружается в каждую сессию, держится компактным (не подвергается компакции). Глубокий архитектурный контекст — по требованию в `docs/ARCHITECTURE.md`. Исторические роадмапы — в `docs/roadmaps/` (legacy-архив `.kilo_old/` сохранен для справки).

## Стек и Окружение

- **Python 3.14**, **FastAPI** (ASGI) поверх Starlette, сервер — uvicorn.
- **Окружение**: Обязательно использовать `venv` проекта (`venv/bin/python`, `venv/bin/pytest`). Никаких установок и правок на уровне системной ОС!
- **Структура**: Монолит `app.py` (~4186 строк) + паттерн `managers/*.py` (по менеджеру на протокол).
- **Секреты**: Симметричное Fernet-шифрование (`managers/secrets.py`, `MASTER_KEY` из env, формат `v1:`).
- **Деплой**: Docker Compose (non-root, read-only rootfs, cap_drop ALL) + опциональный публичный туннель (cloudflared/ngrok, in-process). Dev-режим — `AWP_DEV=1`.

## Эффективность Контекста

- `app.py` = ~4186 строк / 168KB. **Не читать целиком.** Эффективный паттерн: Grep по сигнатуре/декоратору → `view_file` конкретного диапазона (`StartLine`/`EndLine`).
- `telegram_bot.py` ~51KB — аналогично: точечное чтение по Grep.
- **Глубокая архитектура** (схема данных, потоки, реестр протоколов, security-границы, паттерн расширения): Grep/Read `docs/ARCHITECTURE.md`.
- **Исследования и фоновые задачи**: Для параллельного поиска по кодовой базе или документации используй субагента `research` (`invoke_subagent`).
- **Артефакты и планирование**: Для крупных фич используй артефакты `implementation_plan.md`, `task.md`, `walkthrough.md`.

## Команды Разработки (строго в venv)

- **Тесты**: `venv/bin/python -m pytest` (pytest/pytest-asyncio/httpx в `requirements-dev.txt`).
- **Проверка синтаксиса**: `venv/bin/python -m py_compile <file>`.
- **Запуск локально**: `AWP_DEV=1` + `docker-compose.yml` + `docker-entrypoint.sh`.

## Карта Файлов (якоря — file:line, подробнее в ARCHITECTURE.md §«Якоря»)

- `app.py` — роутинг, `_check_admin` `app.py:2106`, middleware `app.py:104-124`, `load_data` `app.py:195`, `get_ssh` `app.py:235`, реестр протоколов `app.py:902`/`978`. `DATA_FILE` (`app.py:135`) читается из env (fallback на application-dir); при апгрейде старый путь мигрируется автоматически (`_maybe_migrate_legacy_data_file` `app.py:1687`).
- `config.py` — env-конфигурация (`SECRET_KEY`, `MASTER_KEY`, `AWP_DEV`, rate-limits).
- `middleware.py` — CSRF (double-submit), SecurityHeaders.
- `managers/ssh_manager.py` — paramiko, **пиннинг отпечатка после успешного handshake** (`ssh_manager.py:104-114`), sudo через stdin (не cmdline).
- `managers/secrets.py` — Fernet, формат `v1:`.
- `managers/{awg,wireguard,xray,telemt,dns,adguard,nginx,socks5,backup}_manager.py` — протоколы (duck-typed интерфейс).
- `managers/xray_manager.py` — `build_initial_server_json` (`xray_manager.py:13`) — чистая функция-билдер `server.json` (VLESS-Reality + Stats + API).
- `scripts/migrate_secrets.py` — миграция plaintext-секретов в `v1:` (отказ при `AWP_DEV=1` без `--force`).
- `tests/{test_auth,test_api_tokens,test_helpers,test_secrets,test_share}.py`, `tests/managers/test_{awg,wireguard,xray_config}_*.py`, `tests/conftest.py`.

## Безопасность

- Все секреты в `data.json` — только через Fernet (`managers/secrets.py`); SSH-креды хранятся как `v1:`.
- `SECRET_KEY` и `MASTER_KEY` обязательны из env; без них (кроме `AWP_DEV=1`) — refuse to start.
- SSH-пароли — через stdin-канал paramiko, **не** через `echo '<p>' | sudo -S` (не должны светиться в `/proc/<pid>/cmdline`).
- Пользовательские строки (domain/email/username) перед интерполяцией в sudo-команды — валидировать (пример: `nginx_manager._validate_domain`/`_validate_email`); пути квотировать `shlex.quote`.
- `data.json` содержит секреты — не коммитить (в `.gitignore` вместе с `.env`, `*.local`, `venv/`).

## Паттерн Расширения

Добавление нового протокола: регистрация в `app.py` (`BASE_PROTOCOLS` `app.py:902`, `get_protocol_manager` `app.py:978`, мапы имён/контейнеров) → новый `managers/foo_manager.py` (duck-typed, клиентские методы с `protocol_type` первым аргументом) → ветка установки в `api_install_protocol` (`app.py:2590`). Подробный чек-лист на 7 шагов — `docs/ARCHITECTURE.md` §13.
