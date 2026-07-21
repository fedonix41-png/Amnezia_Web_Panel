# План A: закрыть тестовые/CI/документационные дыры MVP-security

**Контекст:** security-ядро планов `1784441633878-mvp-security-cloak-roadmap.md` (Этапы 1.1–1.6, 2.1) и `1784460524479-context-token-optimization.md` полностью реализовано. Этот план закрывает оставшиеся дыры этапов 2.4, 3.2, 3.3, 3.4 и 1.5 (README), выявленные при сверке кодовой базы.

**Принцип:** минимум нового кода в `app.py`/`managers/`; фокус — тесты, CI-конфиг, документы. Единственная мини-правка исходника — экстракция чистой функции-билдера `server.json` из `xray_manager.install_protocol` ради тестируемости.

---

## Этап 1. Верифицировать текущее состояние тестов (до правок)

`venv/bin/python -m pytest -v` (использовать проектный venv, **не** системный Python — падает с `ModuleNotFoundError`).

**Подтвердить или опровергнуть память `test_auth_py_known_failures`:** в коде (`app.py:2041-2048`) captcha-gate срабатывает только если `data['settings']['captcha']['enabled'] is True`. Startup (`app.py:1683-1751`) НЕ сетит `captcha.enabled=True` — только `ssl`. Фикстура `conftest.py:25` сдает `"settings": {}` → gate должен быть закрыт, тесты должны проходить.

- Если **5 тестов реально падают** — найти, кто включает captcha (например, env или миграция, не видимая в стартапе), и зафиксировать root cause в новом memory через `kilo_memory_save(action: correct)`.
- Если **тесты проходят** — пометить память `test_auth_py_known_failures` как устаревшую (`kilo_memory_save` action: forget), и пропустить Этап 3.

**Это блокирующая разведка перед Этапом 3.** Не «чинить» тесты, не зная реальной причины.

---

## Этап 2. CI: Python 3.14 + coverage-порог

**Файлы:** `.github/workflows/tests.yml`, `requirements-dev.txt`.

- [ ] 2.1. В `requirements-dev.txt` добавить `pytest-cov>=5,<7`.
- [ ] 2.2. В `.github/workflows/tests.yml` matrix добавить `"3.14"` (фактический runtime проекта — `Dockerfile` `python:3.14-slim`, AGENTS.md фиксирует Python 3.14). Матрица станет `["3.10", "3.11", "3.12", "3.13", "3.14"]`.
- [ ] 2.3. Шаг «Run tests» заменить на:
  ```yaml
  - name: Run tests with coverage
    run: pytest --cov=app --cov=managers --cov=middleware --cov=config --cov-fail-under=40 --cov-report=term-missing
    env:
      AWP_DEV: "1"
  ```
  Порог 40% — соответствует плану `1784441633878` §3.4. Поднимать позже.

---

## Этап 3. Адаптировать `test_auth.py` (только если Этап 1 подтвердил падения)

**Принцип:** не ослаблять assertions; либо фиксить окружение, либо явно `xfail` с issue-ссылкой.

- [ ] 3.1. Если captcha действительно включается (например, через env или дефолт-настройки не из startup): расширить фикстуру `app_client` в `conftest.py` — после `_startup` записать в `data.json` `settings.captcha.enabled = False`, чтобы тесты детерминированно шли по бескапчевому пути. Для отдельного покрытия капчи добавить `TestCaptcha.test_login_with_correct_captcha` / `test_login_with_wrong_captcha`.
- [ ] 3.2. Альтернатива (если captcha обязателен по security-политике): добавить хелпер `_login(client, user, pw, captcha=None)`, который сначала `GET /api/auth/captcha`, читает `request.session['captcha_answer']` (через дополнительный debug-эндпоинт или через инжект в сессию в тестовом режиме), передаёт правильное значение. Предпочтительнее вариант 3.1 — captcha должна быть тестируемой отдельным классом, а не ломать логин-тесты.
- [ ] 3.3. `xfail` с `strict=True` и комментарием-ссылкой на issue — только если 3.1/3.2 невозможны в рамках плана. Записать решение в memory.

---

## Этап 4. Новый файл `tests/test_api_tokens.py`

**Подготовка:** общий хелпер-логин в `tests/conftest.py` или локально в файле.

```python
def _login_as_admin(client):
    client.get("/login")  # seed csrf cookie
    csrf = client.cookies.get("csrf_token", "")
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "admin"},
                    headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
```

Тест-кейсы (все на `app_client`, после `_login_as_admin`; `limiter.reset()` перед сериями):

- [ ] 4.1. **`TestApiTokenLifecycle`**
  - `test_create_token_returns_raw_once` — POST `/api/settings/tokens` с `{"name":"ci"}` → 200, в теле есть `token` с префиксом `awp_`, есть `token_prefix`, `id`.
  - `test_list_tokens_does_not_expose_raw` — после создания, GET `/api/settings/tokens` → токен в списке, НО поля `token`/`token_hash` НЕТ, только `token_prefix`, `name`, `created_at`, `owner`.
  - `test_revoke_token` — DELETE `/api/settings/tokens/{id}` → 200; повторный DELETE → 404.
  - `test_revoke_unknown_returns_404`.
  - `test_empty_name_rejected` — POST с `{"name":""}` → 400.
- [ ] 4.2. **`TestBearerAuth`** (без session-cookie, без CSRF):
  - `test_bearer_grants_admin_access` — создать токен → `GET /api/settings/tokens` с `Authorization: Bearer <raw>` без CSRF → 200 (Bearer exempt от CSRF по `middleware.py:_is_bearer`).
  - `test_bearer_revoked_after_delete` — после revoke тот же Bearer → 403.
  - `test_bearer_unknown_returns_403` — `Bearer awp_nope` → 403, не 500.
  - `test_bearer_token_prefix_only_rejected` — Bearer = `token_prefix` (не полный raw) → 403.
- [ ] 4.3. **`TestTokenOwnerState`** — прямая запись в `data.json` через фикстуру:
  - `test_disabled_owner_token_stops_working` — у владельца `enabled=False` → Bearer → 403 (`_resolve_api_token:1094`).
  - `test_downgraded_owner_token_stops_working` — владелец `role='user'` → Bearer → 403 (`_resolve_api_token:1096`).
- [ ] 4.4. **`TestTokenHashing`** (unit на `app._hash_api_token`):
  - `test_hash_is_sha256_hex` — 64 hex char.
  - `test_hash_is_one_way` — разные raw → разные hash; raw не восстанавливается из hash.

---

## Этап 5. Мини-рефакторинг `xray_manager.py` + `tests/managers/test_xray_config.py`

**Цель:** проверить структуру генерируемого `server.json` без SSH. Сейчас билдер инлайнен в `install_protocol` (`xray_manager.py:245-299`) и зависит от `self.ssh`.

- [ ] 5.1. **Экстракция** (единственная правка исходника в этом плане): вынести построение `server_json` в модульную функцию:
  ```python
  # managers/xray_manager.py — module level, вне класса
  def build_initial_server_json(*, port: int, site_name: str, private_key: str,
                                public_key: str, short_id: str) -> dict:
      """Build the initial Xray server.json structure with Stats + Reality inbound.
      Pure function — no SSH, no I/O. Extracted from install_protocol for testability."""
      ...  # содержимое 245-299, параметризованное
  ```
  В `install_protocol` заменить inline-блок на `server_json = build_initial_server_json(port=int(port), site_name=site_name, private_key=priv_key, public_key=pub_key, short_id=short_id)`. Поведение идентично (включая `int(port)`).
- [ ] 5.2. **`tests/managers/test_xray_config.py`** — чистые проверки структуры:
  - `test_has_vless_inbound_on_port` — `inbounds[0].protocol=='vless'`, `port` совпадает.
  - `test_reality_stream_settings` — `streamSettings.security=='reality'`, `realitySettings.dest==f"{site}:443"`, `serverNames==[site]`, `privateKey`/`shortIds` заполнены.
  - `test_api_inbound_on_loopback` — второй inbound: `listen=='127.0.0.1'`, `port==10085`, `protocol=='dokodemo-door'`, `tag=='api'`.
  - `test_routing_directs_api_inbound_to_api_outbound` — правило `inboundTag==['api']` → `outboundTag=='api'`.
  - `test_stats_and_policy_present` — `stats=={}`, `policy.levels.0.statsUserUplink is True`, `policy.system.statsInboundUplink is True`.
  - `test_outbounds_freedom_default` — `outbounds[0].protocol=='freedom'`.
  - `test_port_is_int_coerced` — `build_initial_server_json(port="443", ...)` → `inbounds[0].port == 444 (int)`.
  - `test_short_id_appears_in_shortids_list`.

---

## Этап 6. `docs/mvp-smoke-checklist.md`

Чек-лист ручной валидации MVP (требование плана `1784441633878` §2.4). Структура — копируется в файл практически дословно:

- **Локальный подъём:** `cp .env.example .env` → заполнить `SECRET_KEY` (≥32 байт) и `MASTER_KEY` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) → `docker compose up`.
- **Контейнер здоров:** `docker ps` показывает `amnezia_panel` (healthy через ~40s); `docker compose logs` без `Refusing to start`.
- **Логин:** браузер `http://localhost:${APP_PORT}` → `/login` → `admin/admin` → смена пароля.
- **Сервер:** добавить тестовый сервер (можно mock-SSH или реальный Ubuntu). При повторном edit-сейве `host_fingerprint` не должен запрашиваться заново.
- **Протокол:** установить WireGuard/AWG на реальном сервере. Создать клиента → скачать конфиг → подключиться.
- **Connection lifecycle:** создать user_connection → удалить → проверить очистку.
- **Share-ссылка:** `/settings` → включить share → открыть share-URL → ввод share-password → конфиг доступен.
- **Шифрование:** `sudo docker exec amnezia_panel cat /app/data/data.json | grep -E '"(password|private_key)":\s*"[^v]'` → **0 совпадений** (все секреты имеют префикс `v1:`).
- **Стабильность сессии:** `docker compose restart` → браузер без re-login остаётся залогинен (стабильный `SECRET_KEY`).
- **Rate-limit:** 7 быстрых `POST /api/auth/login` с wrong password → 6-я/7-я возвращают `429`.
- **CSRF:** `curl -X POST http://localhost:${APP_PORT}/api/servers/add -H 'Content-Type: application/json' -d '{"host":"x","username":"y"}'` без `X-CSRF-Token` → `403 CSRF token missing or invalid`.
- **Backup:** подождать `BACKUP_INTERVAL_HOURS` (или поставить `=1` для теста) → `ls /app/data/backups/` показывает свежий шифрованный файл.
- **Публичный туннель:** `/settings` → Cloudflare Quick Tunnel → получить `https://*.trycloudflare.com` → повторить логин/сервер/протокол через публичный URL. Настройка `TRUSTED_HOSTS` включает домен туннеля.
- **Брутфорс-лог:** попытки подобрать пароль логируются; rate-limit не даёт >5/мин.

---

## Этап 7. `README.md` — секция «Deployment via tunnel»

После существующего блока про туннели (текущая строка 107) добавить отдельный развёрнутый раздел:

- **Обязательные env-переменные** — таблица: `SECRET_KEY`, `MASTER_KEY`, `TRUSTED_HOSTS`, `SESSION_COOKIE_SECURE`, `SESSION_COOKIE_SAMESITE`, `LOGIN_RATE_LIMIT`, `SHARE_RATE_LIMIT`, `BACKUP_INTERVAL_HOURS`, `AWP_DEV` — со ссылкой на `.env.example` как на источник истины.
- **Quick start:** 3 команды — `cp .env.example .env`, генерация `MASTER_KEY`, `docker compose up -d`.
- **Открытие в публичный туннель:** после поднятия панели → `/settings` → Cloudflare Quick Tunnel или ngrok; указать домен туннеля в `TRUSTED_HOSTS` (иначе `TrustedHostMiddleware` даст 400).
- **Security notes:**绝不 не коммитить `.env` / `data.json`; `MASTER_KEY` потерян = SSH-секреты unreadable навсегда; `SECRET_KEY` сменён = все сессии инвалидируются; стабильность обоих ключей критична для prod.
- **Dev-режим:** `AWP_DEV=1` синтезирует эфемерные ключи — только локально, никогда в prod; `migrate_secrets.py` отказывается работать в dev без `--force`.

---

## Валидация плана (критерии готовности)

1. `venv/bin/python -m pytest -v` — все тесты зелёные (или явно `xfail strict`).
2. `venv/bin/python -m pytest --cov=app --cov=managers --cov=middleware --cov=config --cov-fail-under=40` проходит локально.
3. `python -m py_compile app.py managers/xray_manager.py` — без ошибок (AGENTS.md: lint/typecheck не настроены, `py_compile` — единственный статический контроль).
4. Push в ветку `feat/mvp-test-gaps` → GitHub Actions matrix `["3.10"..."3.14"]` зелёная, coverage-step проходит.
5. `docs/mvp-smoke-checklist.md` существует и покрывает все 13 пунктов.
6. `README.md` содержит секцию с таблицей env-переменных.
7. Если Этап 1 подтвердил устаревшую память `test_auth_py_known_failures` — `kilo_memory_save(action: forget, query: "test_auth_py_known_failures")`. Если подтверждил реальный баг — `kilo_memory_save(action: correct)` с новым root cause.

---

## Риски и митигация

| Риск | Митигация |
|---|---|
| Этап 1 показывает, что тесты уже зелёные | Этап 3 пропускается; забыть память. Экономим effort. |
| Captcha включается через неочевидный путь (e.g. env) | Сначала grep по `captcha` по всему репо; если включается env-переменной — фикс в `conftest.py`. |
| `pytest-cov` конфликтует с текущим pytest 8.x | Версии пинены: `pytest-cov>=5,<7` совместим с pytest 8.x. |
| Python 3.14 в CI падает из-за несовместимости зависимостей | `fail-fast: false` уже в matrix — 3.14 не блокирует остальные. Если падает — добавить `allow-failure`-style через `continue-on-error: true` на отдельном job. |
| Рефакторинг `build_initial_server_json` ломает install_protocol | Экстракция 1-в-1 (тот же dict literal); покрыть регрессию явным `test_install_protocol_uses_builder` (опц.). |
| `_resolve_api_token` тесты требуют доступа к `data.json` во время сессии | Фикстура `app_client` уже сдает изолированный `data.json` — записывать напрямую в файл и вызывать `load_data()` заново. |

---

## Вне scope (явно)

- Этап 4 плана `1784441633878` (OpenVPN over Cloak) — отдельный план.
- Git fork redirect (Этап 0.2 оригинала) — отдельная операционная задача.
- 2FA / audit-log — post-MVP.
- Внедрение ruff/mypy — отдельная задача (AGENTS.md пока фиксирует `py_compile` + pytest).
- Увеличение coverage выше 40% — итеративно после закрытия базовых дыр.
