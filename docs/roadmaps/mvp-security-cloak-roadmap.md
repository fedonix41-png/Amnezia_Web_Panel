# План доработок до MVP: безопасность + OpenVPN over Cloak

**Проект:** Amnezia Web Panel (форк `PRVTPRO/Amnezia-Web-Panel`, коммит `dd8bda3` v1.5.0)
**Цель:** закрыть критичные дыры безопасности, стабилизировать MVP для тестирования через Cloudflare/ngrok tunnel, затем добавить OpenVPN over Cloak.
**Форма деплоя:** Docker Compose + публичный туннель (существующая фича проекта).
**Защита секретов:** симметричное шифрование Fernet по `MASTER_KEY` из env.

---

## Контекст и подтверждённые проблемы

Проверено чтением кода (`app.py`, `managers/*.py`, `requirements.txt`, `docker-compose.yml`, `Dockerfile`):

| # | Проблема | Где | Severity |
|---|---|---|---|
| S1 | SSH-пароли и приватные ключи серверов хранятся в `data.json` в открытом виде | `app.py:2036, 2093` (`'password': req.password`) | **Критично** |
| S2 | `SECRET_KEY` по умолчанию генерируется случайно при каждом старте → инвалидация сессий + небезопасная дефолт-конфигурация Docker | `app.py:88` | **Критично** для tunnel-деплоя |
| S3 | `paramiko.AutoAddPolicy()` — принимается любой SSH-хост, возможен MITM при первом подключении | `managers/ssh_manager.py:29` | Высокий |
| S4 | SSH-пароль прокидывается в sudo через `echo '<pass>' \| sudo -S` — виден в `/proc/<pid>/cmdline` | `managers/ssh_manager.py:95-96, 114-117` | Высокий |
| S5 | Нет rate-limiting на `/api/auth/login` и `/share/*` | весь `app.py` | Высокий при публичном деплое |
| S6 | Нет CSRF-защиты для cookie-сессий, `SameSite` куки не настроен | `app.py:88` SessionMiddleware без аргументов | Средний |
| S7 | Нет `TrustedHostMiddleware` → Host-header injection | `app.py` | Средний |
| S8 | `data.json` не бэкапится автоматически (только ручной BackupManager) | `app.py` | Средний |
| A1 | Нет реестра протоколов — каждый роут хардкодит `protocol_type` строкой, 51 вызов `_check_admin` без диспатча | весь `app.py` | Архитектурный долг |
| A2 | Тестов ноль — `pytest` не в requirements, `test_*` не найдено | — | Качество |
| A3 | `Dockerfile` пишет под root, нет USER-директивы, нет healthcheck в самом image | `Dockerfile` | Безопасность контейнера |

**Из найденного — что НЕ делаем в MVP (вне scope):**
- Миграция на SQLite/PostgreSQL (упомянут автором как платная опция).
- Замена `data.json` как формата хранения — оставляем, но шифруем поля.
- Полноценный plugin registry — делаем минимальный dispatch-хелпер, не рефакторим всё.

---

## Этап 0. Подготовка dev-окружения и форка (1 шаг)

**Цель:** изолировать свою разработку от upstream.

- [ ] 0.1. Форкнуть `PRVTPRO/Amnezia-Web-Panel` → свой GitHub-аккаунт.
- [ ] 0.2. Перенастроить remotes:
  ```bash
  git remote rename origin upstream
  git remote add origin https://github.com/<твой-ник>/Amnezia-Web-Panel.git
  git push -u origin main
  ```
- [ ] 0.3. Создать рабочую ветку `feat/mvp-security`.
- [ ] 0.4. Добавить в `.gitignore`: `openapi.json`, `.env`, `*.local`, `.kilo/` (если не личное).
- [ ] 0.5. Создать `.env.example` со всеми переменными (см. Этап 1) — сам `.env` не коммитить.

---

## Этап 1. Безопасность — КРИТИЧНО, делать первым (блокирует MVP)

Порядок внутри этапа соответствует зависимости шагов.

### 1.1. Стабильный `SECRET_KEY` + env-конфигурация

- [ ] Изменить `app.py:88`: убрать `secrets.token_hex(32)` как fallback. При отсутствии `SECRET_KEY` в env — **refuse to start** (кроме явного dev-режима `AWP_DEV=1`).
- [ ] Добавить `python-dotenv` загрузку `.env` при старте (`load_dotenv()` в `app.py` самом верху). `python-dotenv==1.2.2` уже в `requirements.txt`.
- [ ] В `docker-compose.yml` прокинуть `environment:` блок со всеми секретами из `.env`.
- [ ] В `Dockerfile` НЕ вшивать секреты — только `ENV` для нечувствительных настроек.

**Env-переменные (финальный список для всего MVP):**
```
SECRET_KEY=<обязательно, минимум 32 байта>
MASTER_KEY=<Fernet key, см. 1.2>
AWP_DEV=0|1                # отключает fail-fast проверки, только локально
APP_PORT=5000
TRUSTED_HOSTS=panel.example.com,localhost
SESSION_COOKIE_SAMESITE=lax|strict
SESSION_COOKIE_SECURE=true|false
LOGIN_RATE_LIMIT=5/minute
SHARE_RATE_LIMIT=10/minute
```

### 1.2. Шифрование SSH-секретов через Fernet (главная задача этапа)

- [ ] Создать `managers/secrets.py` с функциями:
  ```python
  def encrypt_secret(plaintext: str) -> str  # возвращает "v1:<base64-token>"
  def decrypt_secret(token: str) -> str       # понимает префикс версии
  def _get_fernet() -> Fernet                 # читает MASTER_KEY, fail-fast
  ```
  Префикс `v1:` нужен, чтобы отличать уже зашифрованные поля от legacy-plaintext при миграции.
- [ ] `MASTER_KEY` генерировать один раз командой `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` и класть в `.env`. В коде — fail-fast, если отсутствует или невалиден.
- [ ] Везде, где пишется/читается `server['password']` и `server['private_key']`:
  - запись: `encrypt_secret(...)` (4 места: `api_add_server` 2036, `api_edit_server` 2093, sync из Remnawave, любой импорт).
  - чтение: `decrypt_secret(...)` перед передачей в `SSHManager(...)` (везде, где создаётся `SSHManager`).
- [ ] Создать скрипт-миграцию `scripts/migrate_secrets.py` — читает `data.json`, для каждого поля без префикса `v1:` шифрует и перезаписывает. Backup `data.json.bak` обязателен. Запуск: `python scripts/migrate_secrets.py`.
- [ ] Написать smoke-тест (см. Этап 3): зашифровать → расшифровать → сравнить. Тест миграции на тестовом `data.json` с plaintext-полями.

### 1.3. Защита SSH-подключений

- [ ] `managers/ssh_manager.py:29`: заменить `AutoAddPolicy()` на `RejectPolicy()` по умолчанию. Добавить в модель сервера поле `host_fingerprint` (sha256) — при первом подключении сохранять, при последующих сравнивать. UI: кнопка «Сбросить fingerprint» если ключ сервера изменился легально.
- [ ] `managers/ssh_manager.py:95-117`: убрать прокидывание пароля через `echo '<pass>' | sudo -S`. Альтернатива: записывать пароль во временный файл `chmod 600`, передавать через `sudo -S < file`, удалять сразу после. Или (предпочтительно): при первом подключении настраивать passwordless sudo для конкретных команд Amnezia через `/etc/sudoers.d/amnezia-panel` и далее работать без пароля.

### 1.4. HTTP-слой защиты (для публичного деплоя через tunnel)

- [ ] Добавить `TrustedHostMiddleware` (из `starlette.middleware.trustedhost`) с `TRUSTED_HOSTS` из env.
- [ ] Настроить `SessionMiddleware`: `https_only=True` при деплое через HTTPS-туннель, `same_site=SESSION_COOKIE_SAMESITE` (по умолчанию `lax`).
- [ ] Добавить rate-limiting через `slowapi` (новая зависимость в `requirements.txt`):
  - `/api/auth/login`: 5 попыток/минуту с одного IP.
  - `/share/{token}` и связанные: 10 запросов/минуту.
  - `/api/auth/captcha`: 30/минуту.
- [ ] Добавить простой CSRF-токен для cookie-аутентифицированных POST-запросов: при логине класть `csrf_token` в сессию, требовать совпадение с `X-CSRF-Token` header во всех state-changing запросах. Bearer-token-запросы иммунны (токен не уходит автоматически с куками).
- [ ] Добавить security headers middleware: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Content-Security-Policy` минимально-достаточный для работы UI.

### 1.5. Контейнер и деплой

- [ ] `Dockerfile`: добавить `RUN useradd -r -u 1000 panel && USER panel`, копировать файлы с правильным owner, том для `data.json` с правами `0600`.
- [ ] `docker-compose.yml`: добавить `read_only: true` + `tmpfs` для `/tmp`, `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`.
- [ ] В `docker-entrypoint.sh` (если нет — создать): проверять наличие `SECRET_KEY` и `MASTER_KEY`, падать с понятным сообщением, если их нет.
- [ ] Документация: блок в `README.md` «Deployment via tunnel» — какой именно env обязателен.

### 1.6. Автоматический backup `data.json`

- [ ] Background-задача (через `asyncio` + существующий startup hook): раз в N часов копировать `data.json` в `backups/data-YYYYMMDD-HHMMSS.json`, хранить последние N копий (например 14). Все бэкапы — шифрованные (т.к. SSH-поля уже зашифрованы по 1.2).
- [ ] Добавить env `BACKUP_INTERVAL_HOURS=6`, `BACKUP_KEEP_COUNT=14`.

---

## Этап 2. Стабилизация MVP для тестирования

- [ ] 2.1. Заменить устаревший `@app.on_event("startup")` на современный `lifespan` handler (DeprecationWarning, `app.py:1642`).
- [ ] 2.2. Обернуть всё risky-логику в try/except с понятными HTTP-ответами — пройти по всем эндпоинтам, убедиться, что `500` не возвращают stack trace в JSON (только в server logs).
- [ ] 2.3. Проверить graceful shutdown: при `SIGTERM` (Docker stop) корректно закрывать активные SSH-сессии, не оставлять зомби-процессы туннелей.
- [ ] 2.4. Smoke-прогон по чек-листу в `docs/mvp-smoke-checklist.md` (создать):
  - логин admin/admin → смена пароля,
  - добавление тестового сервера (можно mock SSH),
  - установка WireGuard (если есть реальный тестовый сервер),
  - создание/удаление connection,
  - работа share-ссылки,
  - проверка что `data.json` содержит только `v1:` зашифрованные поля,
  - проверка что после рестарта контейнера сессии НЕ инвалидируются (стабильный `SECRET_KEY`).

---

## Этап 3. Тесты (pytest на критичное)

- [ ] 3.1. Добавить `pytest`, `pytest-asyncio`, `httpx` (для TestClient) в `requirements-dev.txt` (новый файл).
- [ ] 3.2. Структура:
  ```
  tests/
  ├── conftest.py             # фикстуры: test app, mock SSH, временный data.json
  ├── test_secrets.py         # encrypt/decrypt, миграция plaintext -> v1
  ├── test_auth.py            # логин, капча, rate-limit, CSRF
  ├── test_api_tokens.py      # выпуск/использование/отзыв bearer
  ├── test_share.py           # share-ссылки, пароль share
  └── managers/
      ├── test_awg_keys.py    # generate_wg_keypair, generate_awg_params (детерминированные проверки)
      ├── test_wireguard_keys.py
      └── test_xray_config.py # базовая валидность генерируемого server.json
  ```
- [ ] 3.3. CI: файл `.github/workflows/tests.yml` — `pytest` на push/PR для python 3.10/3.11/3.12/3.13.
- [ ] 3.4. Покрытие: цель **не 100%**, а покрыть критичные для безопасности пути + ключевые генераторы ключей/конфигов. Целевой порог в CI: 40%, потом поднимать.

---

## Этап 4. OpenVPN over Cloak — НОВАЯ ФИЧА (после безопасности)

**Важно:** Cloak (https://github.com/cbeuw/Cloak) — это не VPN, а transport-layer obfuscator, который работает **поверх** другого VPN (обычно OpenVPN или Shadowsocks). Архитектурно это 2 контейнера: OpenVPN-сервер + ck-server. Клиент тоже использует 2 программы: OpenVPN-клиент + ck-client.

### 4.1. Исследование и дизайн (сначала)

- [ ] Изучить офиц. Amnezia-клиент: использует ли он Cloak? Если да — какой формат клиентских конфигов. Это определит совместимость.
- [ ] Выбрать docker-образы:
  - OpenVPN: `linuxserver/openvpn-as` или официальные скрипты install (как делает AWG-менеджер).
  - Cloak: собирать из исходников `cbeuw/Cloak` или найти готовый community image.
- [ ] Определить сетевую топологию: ck-server слушает публичный порт (например 443), проксирует трафик на локальный OpenVPN-порт. Конфиги клиентов содержат оба слоя.
- [ ] Согласовать с пользователем формат клиентского конфига (как склеивать OpenVPN .ovpn + ck-параметры в один distributable файл) — **это открытый вопрос к уточнению на момент реализации**.

### 4.2. Реализация менеджера

- [ ] Создать `managers/openvpn_cloak_manager.py` по образцу `awg_manager.py`:
  - Класс `OpenVPNOverCloakManager` с тем же интерфейсом (`install_protocol`, `uninstall`, `add_client`, `remove_client`, `get_config`, etc.).
  - Константы: `CONTAINER_NAME`, `DOCKER_IMAGE`, `CONFIG_PATH`, `PROTOCOL = 'openvpn_cloak'`.
  - Генерация ключей: TLS-сертификат OpenVPN (через `cryptography` или `easy-rsa`), keypair Cloak (`ck-server keygen`).
  - Bash-скрипты установки/удаления по аналогии с `awg_manager.install_docker()`.
- [ ] Зарегистрировать протокол в `app.py`:
  - Pydantic-модели для install-запроса (порт, обфускация-параметры Cloak: `UID`, `PublicKey`, `BrowserType`).
  - Роуты в тег-группе `Protocols` (вписать в `OPENAPI_TAGS`, `app.py:52`): `/api/servers/{id}/protocols/openvpn_cloak/install`, `/uninstall`, `/clients/add`, и т.д.
  - Диспетчер «если протокол начинается с `openvpn_cloak` → `OpenVPNOverCloakManager`». Минимальный вариант — if/elif рядом с существующими; опционально — мини-реестр `PROTOCOL_MANAGERS = {...}`.
- [ ] UI: добавить блок в шаблон сервера (`templates/server_detail.html` или аналог) — кнопка install/uninstall, список клиентов, форма редактирования параметров Cloak.
- [ ] i18n: добавить ключи в `translations/en.json` и `translations/ru.json` минимум (остальные языки — потом).

### 4.3. Тесты

- [ ] `tests/managers/test_openvpn_cloak_config.py`: проверка генерации server.conf, ck-server.json, клиентского .ovpn (без реального SSH — на моках).
- [ ] При наличии тестового сервера: smoke-тест install → add client → download config → подключение с реального клиента.

---

## Открытые вопросы (требуют решения в момент реализации)

1. **Cloak: формат клиентского конфига.** Как именно склеивать OpenVPN .ovpn + ck-client параметры для конечного пользователя? Варианты: (a) два отдельных файла + инструкция; (b) один .ovpn с embedded ck-параметрами через script-security; (c) распространять через Amnezia-совместимый формат, если он поддерживает Cloak. **Решить до старта 4.2.**
2. **Passwordless sudo vs temp-file.** Похоже на S4 — выбрать подход (предпочтительнее `/etc/sudoers.d/amnezia-panel` whitelist, но требует проверки что не сломает текущие сервера).
3. **Host fingerprint UX.** Что показывать пользователю при изменении ключа сервера? Подсказка в UI с кнопкой «доверяться новому ключу».

---

## Валидация готовности MVP

MVP считается готовым, когда выполнены ВСЕ пункты:

- [ ] Этапы 0, 1, 2, 3 завершены.
- [ ] `docker compose up` поднимает панель с всеми env-переменными из `.env.example`.
- [ ] `pytest` зелёный в CI.
- [ ] Smoke-чек-лист (2.4) пройден вручную.
- [ ] После деплоя через Cloudflare Quick Tunnel: login → управление сервером → установка протокола работают; rate-limit срабатывает при брутфорсе login; CSRF блокирует кросс-доменные POST.
- [ ] `data.json` (и его бэкапы) не содержит ни одного plaintext SSH-пароля или приватного ключа — проверено `grep` по файлу.
- [ ] Этап 4 (Cloak) — отдельным PR после MVP.

---

## Рекомендуемый порядок работы (зависимости)

```
Этап 0 (форк + .env.example)
   ↓
Этап 1.1 (SECRET_KEY)
   ↓
Этап 1.2 (Fernet шифрование)  ←  самая большая задача
   ↓
Этап 1.3 (SSH hardening)     ←  можно параллельно с 1.4
Этап 1.4 (HTTP middleware)   ←  
   ↓
Этап 1.5 + 1.6 (Docker + backup)
   ↓
Этап 2 (стабилизация)
   ↓
Этап 3 (тесты)  ←  можно писать параллельно с 1.x
   ↓
=== MVP готов ===
   ↓
Этап 4 (OpenVPN over Cloak)
```

---

## Что НЕ входит в MVP (явно out of scope)

- Миграция на SQLite/PostgreSQL.
- Полноценная plugin-архитектура протоколов.
- In-panel редактор конфигов контейнеров.
- Продвинутые бэкапы (внешнее хранилище, шедулер в cron-формате).
- Другие протоколы помимо Cloak (SSH-tunnel, Shadowsocks-over-X) — после Cloak по тому же паттерну.
- 2FA для админских аккаунтов (стоит добавить в roadmap сразу после MVP).
- Аудит-лог действий администраторов (тоже post-MVP).
