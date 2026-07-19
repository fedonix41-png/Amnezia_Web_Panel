# Amnezia Web Panel — AGENTS.md

Это **lean-роутер**: автозагружается в каждую сессию, держится компактным (не подвергается compaction). Глубокий архитектурный контекст — по требованию в `docs/ARCHITECTURE.md`.

## Стек

- **Python 3.14**, **FastAPI** (ASGI) поверх Starlette, сервер — uvicorn.
- Монолит `app.py` (~4143 строк) + паттерн `managers/*.py` (по менеджеру на протокол).
- Секреты — симметричное Fernet-шифрование (`managers/secrets.py`, `MASTER_KEY` из env, формат `v1:`).
- Деплой: Docker Compose (non-root, read-only rootfs, cap_drop ALL) + опциональный публичный туннель (cloudflared/ngrok, in-process). Dev-режим — `AWP_DEV=1`.

## Контекстные правила (КРИТИЧНО — экономия токенов)

- `app.py` = ~4143 строки / 170KB. **НИКОГДА не читать целиком.** Паттерн: Grep по сигнатуре/декоратору → Read конкретного диапазона (offset/limit) → ссылка `file:line`.
- `telegram_bot.py` ~51KB — аналогично.
- **Глубокий контекст** (схема данных, потоки, реестр протоколов, security-границы, паттерн расширения): Grep/Read `docs/ARCHITECTURE.md`. Не подгружай его целиком без нужды.
- Разведка незнакомой области — делегируй Task-субагенту (`subagent_type: explore`), не грузи файлы в основной контекст.
- Перед длинной серией правок при большом хвосте сессии — `/compact`.
- Перед CLI-командой может появиться блок `[kcp] ...` (плагин opencode-kcp-plugin) со структурой флагов. **Используй его; не запускай `--help` при наличии `[kcp]`.**

## Команды

- Тесты: `python -m pytest` (pytest/pytest-asyncio/httpx в `requirements-dev.txt`).
- Проверка синтаксиса: `python -m py_compile <file>` (lint/typecheck-тулинга не настроено).
- Запуск локально: `AWP_DEV=1` + `docker-compose.yml` + `docker-entrypoint.sh`.

## Карта файлов (якоря — file:line, подробнее в ARCHITECTURE.md §«Якоря»)

- `app.py` — роутинг, `_check_admin` `app.py:2063`, middleware `app.py:104-124`, `load_data` `app.py:191`, `get_ssh` `app.py:231`, реестр протоколов `app.py:898`/`974`.
- `config.py` — env-конфигурация (`SECRET_KEY`, `MASTER_KEY`, `AWP_DEV`, rate-limits).
- `middleware.py` — CSRF (double-submit), SecurityHeaders.
- `managers/ssh_manager.py` — paramiko, **пиннинг отпечатка после успешного handshake** (`ssh_manager.py:104-114`), sudo через stdin (не cmdline).
- `managers/secrets.py` — Fernet, формат `v1:`.
- `managers/{awg,wireguard,xray,telemt,dns,adguard,nginx,socks5,backup}_manager.py` — протоколы (duck-typed интерфейс, без базового класса).
- `scripts/migrate_secrets.py` — миграция plaintext-секретов в `v1:` (отказ при `AWP_DEV=1` без `--force`).
- `tests/{test_auth,test_secrets,test_share}.py`, `tests/managers/test_*_keys.py`, `tests/conftest.py`.

## Безопасность

- Все секреты в `data.json` — только через Fernet (`managers/secrets.py`); SSH-креды хранятся как `v1:`.
- `SECRET_KEY` и `MASTER_KEY` обязательны из env; без них (кроме `AWP_DEV=1`) — refuse to start.
- SSH-пароли — через stdin-канал paramiko, **не** через `echo '<p>' | sudo -S` (не должны светиться в `/proc/<pid>/cmdline`).
- Пользовательские строки (domain/email/username) перед интерполяцией в sudo-команды — валидировать (пример: `nginx_manager._validate_domain`/`_validate_email`); пути квотировать `shlex.quote`.
- `data.json` содержит секреты — не коммитить (в `.gitignore` вместе с `.env`, `*.local`, `venv/`).

## Паттерн расширения (кратко)

Добавление нового протокола: регистрация в `app.py` (`BASE_PROTOCOLS` `app.py:898`, `get_protocol_manager` `app.py:974`, мапы имён/контейнеров) → новый `managers/foo_manager.py` (duck-typed, клиентские методы с `protocol_type` первым аргументом) → ветка установки в `api_install_protocol` (`app.py:2547`). Подробный чек-лист на 7 шагов — `docs/ARCHITECTURE.md` §13.
