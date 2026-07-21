# План: оптимизация среды разработки Kilo под качество кода и экономию контекста

**Проект:** Amnezia Web Panel (Python, ASGI в `app.py`, паттерн `managers/*.py`, Docker, SSH)
**Цель:** заточить среду Kilo так, чтобы модель не «съедала» контекст пачками и не путалась: минимум стартового системного промпта, ранний предсказуемый compaction, проектные правила, компактные CLI-знания.
**Принцип:** качество кода — приоритет; экономия токенов — без ущерба качеству.

---

## Контекст и подтверждённые проблемы (актуально на 2026-07)

Проверено чтением конфигов и кода:

| # | Проблема | Где | Влияние на контекст |
|---|---|---|---|
| C1 | 5 MCP-серверов грузятся глобально в каждую сессию | `~/.config/kilo/kilo.json` (memory, brave-search) + `kilo.jsonc` (context7, web-search-prime, web-reader) | ~15–25% системного промпта на старте; `memory` дублирует нативные `kilo_memory_*`; `brave-search` дублирует `web-search-prime` |
| C2 | Нет проектного `kilo.json`/`AGENTS.md` | корень проекта | нет проектных правил, модель каждый раз «с холодного старта» изучает структуру |
| C3 | `app.py` = **4143 строки / 170KB** | `app.py` | чтение целиком = катастрофа контекста одной операцией |
| C4 | Модель в конфиге не совпадает с фактической | `kilo.jsonc`: `openrouter/z-ai/glm-5`; факт: `kilo/zai-coding/glm-5.2` | расход на неверный выбор/small_model |
| C5 | Compaction не настроен (дефолт: триггер ~100% минус 20K буфер) | — | поздняя компакция → путаница к концу длинной сессии |
| C6 | 5 кастомных агентов глобально грузятся везде | `kilo.json` | лишние описания в каждом проекте |
| C7 | CLI-heavy проект (Docker/SSH/sudo/git) без CLI-знаний | — | модель гоняет `--help` и шумные выводы (~20–40% окна по данным Discussion #7020) |
| C8 | lint/typecheck-тулинга нет, есть только pytest | `requirements-dev.txt` | невозможно валидировать код типами; надо зафиксировать в AGENTS.md фактические команды |

**Подтверждённые источники 2026:**
- Kilo docs «Context Condensing» (commit `8b5b9a5`, 2026-04): формула триггера, `threshold_percent`, `reserved`, `tail_turns`, `preserve_recent_tokens`, env-оверрайды.
- Блог Kilo «Match skills to models with custom agents» (2026-07-17): метаданные skill дёшевы, полная загрузка `SKILL.md` — дорогая; per-model агенты через `permission.skill`.
- Discussion #7020 / Issue #7021: `opencode-kcp-plugin` экономит ~33% контекста на CLI-сессиях (`ps aux` 30828→652 токенов, −98%).

## Принятые решения (согласовано с пользователем)

1. **Область:** проектный конфиг + точечные правки глобального. Глобальный правится только в части отключения дублей MCP и модели — остальные проекты не страдают.
2. **MCP:** оставить `context7` + `web-search-prime` + `web-reader`; `memory` и `brave-search` отключить (non-destructive, через `enabled:false`).
3. **KCP-плагин:** добавить `opencode-kcp-plugin` (CLI-экономия для Docker/SSH/git).

---

## Этап 1. Точечные правки глобального конфига

**Файл:** `~/.config/kilo/kilo.jsonc` (deep-merge поверх `kilo.json`, приоритет выше).

**1.1.** В блок `mcp` добавить отключение дублей (ключи с API остаются в `kilo.json`, только выключаются):
```jsonc
"mcp": {
  "context7": { "type": "local", "command": ["npx", "-y", "@upstash/context7-mcp"], "environment": { "DEFAULT_MINIMUM_TOKENS": "" } },
  "web-search-prime": { /* без изменений */ },
  "web-reader": { /* без изменений */ },
  "memory": { "enabled": false },
  "brave-search": { "enabled": false }
}
```

**1.2.** Поправить модель и добавить `small_model` (точный ID проверить через `/models` или `<leader>m`):
```jsonc
"model": "kilo/zai-coding/glm-5.2",
"small_model": "kilo/zai-coding/glm-5.2"
```
> Если есть более дешёвый вариант GLM для titles/summaries — поставить его в `small_model`. Цель — не гонять дорогую модель на служебных операциях.

**1.3.** Добавить блок compaction (ранний предсказуемый триггер ради связности):
```jsonc
"compaction": {
  "auto": true,
  "threshold_percent": 75,
  "prune": true,
  "tail_turns": 3,
  "reserved": 20000
}
```
> Обоснование: `threshold_percent:75` даёт раннюю компакцию до того, как модель начнёт путаться; `tail_turns:3` (вместо 2) держит больше «свежего» вербатима для связности кода. Трейд-офф: чуть больше токенов на хвост и на сам summarization-вызов — приемлемо ради качества.

**1.4 (опционально).** Дedicированная модель для compaction, если доступна дешёвая с большим окном:
```jsonc
"agent": { "compaction": { "model": "kilo/zai-coding/glm-5.2" } }
```

---

## Этап 2. Проектный конфиг

**Файл (создать):** `.kilo/kilo.jsonc` — deep-merge поверх глобального.

```jsonc
{
  "$schema": "https://app.kilo.ai/config.json",
  "plugin": ["opencode-kcp-plugin"],
  "permission": {
    "read": "allow",
    "edit": "allow",
    "bash": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "external_directory": "ask",
    "task": "allow"
  }
}
```
> Модель/compaction не дублируем — наследуемся из глобального. `external_directory:"ask"` — проект не должен молча читать наружу (кроме предодобренного `/tmp/kilo`), снижает случайный расход.

---

## Этап 3. Установка KCP-плагина (CLI-экономия)

**3.1.** В `.kilo/` уже есть `package.json` + `node_modules` (содержит `@kilocode/plugin@7.4.11`). Добавить зависимость:
```bash
npm install opencode-kcp-plugin --prefix .kilo
```
**3.2.** Плагин уже подключён в проектном конфиге (Этап 2, `"plugin": ["opencode-kcp-plugin"]`).
**3.3.** Проверить: запуск любой CLI-команды (`git status`, `docker ps`) должен предваряться блоком `[kcp] ...`. Если блок не появляется — убедиться, что Kilo видит проектный конфиг (`/status`).

---

## Этап 4. Создать `AGENTS.md` в корне проекта

Это главный рычаг «качество + меньше хаоса»: авто-загружается в каждую сессию, не подвергается compaction, задаёт проектные правила. Содержание (краткое, подструктурированное):

```markdown
# Amnezia Web Panel — AGENTS.md

## Стек
- Python 3, ASGI-приложение в `app.py` (Flask-like роутинг поверх Starlette SessionMiddleware).
- Паттерн `managers/*.py`: по одному менеджеру на протокол.
- Секреты — симметричное шифрование Fernet (`managers/secrets.py`, MASTER_KEY из env).
- Деплой: Docker Compose + публичный туннель; dev-режим через `AWP_DEV=1`.

## Контекстные правила (КРИТИЧНО для экономии токенов)
- `app.py` = 4143 строки / 170KB. НИКОГДА не читать целиком. Только Grep по сигнатуре → Read конкретного диапазона (offset/limit) → `file_path:line`-ссылки.
- `telegram_bot.py` ~51KB — аналогично: Grep → targeted Read.
- Для разведки незнакомой области используй Task-субагента (subagent_type: explore), а не грузи файлы в основной контекст.
- Для поиска файлов — Glob; по содержимому — Grep. Не заменяй их на `find`/`cat`/`head`.
- Перед длинной серией правок — `/compact`, если хвост сессии большой.

## Команды
- Тесты: `python -m pytest` (pytest/pytest-asyncio/httpx в requirements-dev.txt).
- Lint/typecheck: НЕ настроены. При правках контролируй синтаксис `python -m py_compile <file>`.
- Запуск локально: см. docker-compose.yml + docker-entrypoint.sh (AWP_DEV=1).

## Карта файлов (knowledge-индекс, чтобы не искать вслепую)
- `app.py` — роутинг, _check_admin, SessionMiddleware (точечный доступ).
- `config.py` — env-конфигурация.
- `middleware.py` — middleware (rate-limit, trusted host — см. roadmap).
- `managers/ssh_manager.py` — SSH (paramiko, отпечаток после аутентификации).
- `managers/secrets.py` — Fernet-шифрование секретов.
- `managers/{wireguard,awg,xray,adguard,dns,nginx,socks5,telemt,backup}_manager.py` — протоколы.
- `scripts/migrate_secrets.py` — миграция открытых секретов в зашифрованный вид.
- `tests/{test_auth,test_secrets,test_share}.py`, `tests/conftest.py` — pytest.

## Безопасность (из действующего roadmap)
- Никогда не логировать/echo секреты, ключи, пароли.
- Все секреты в `data.json` — только через Fernet (`managers/secrets.py`).
- `SECRET_KEY` обязателен из env; без него — refuse to start (кроме `AWP_DEV=1`).
- SSH-пароли не передавать через `echo '<p>' | sudo -S` (виден в /proc/<pid>/cmdline).
- `.env`, `*.local`, `data.json` — не коммитить (уже в .gitignore).

## KCP
- Перед CLI-командами может появиться блок `[kcp] ...` со структурой флагов. Используй его. НЕ запускай `--help`, если есть `[kcp]`-контекст.
```

---

## Этап 5 (опц., после стабилизации базовой настройки). Per-model агент

Следуя блогу Kilo (2026-07-17): создать `.kilo/agent/deep-work.md`, pinned на GLM 5.2, с политикой загрузки skill только при явном совпадении. Это даёт «одним переключением» менять и модель, и skill-политику. Включать, если будут добавлены тяжёлые skills (superpowers и т.п.).

---

## Валидация (после применения)

1. `/status` — подтвердить: модель `kilo/zai-coding/glm-5.2`, проектный конфиг `.kilo/kilo.jsonc` загружен, плагин `opencode-kcp-plugin` активен.
2. `/mcps` — активны ровно 3 MCP: `context7`, `web-search-prime`, `web-reader`. `memory`/`brave-search` не видны.
3. Стартовая сессия: задать вопрос «какие skills/MCP доступны?» — проверить, что ответ идёт от актуального набора, а стартовый промпт не содержит инструментов memory/brave.
4. Запустить `git status` / `docker ps` — убедиться, что появляется `[kcp]`-блок и не вызывается `--help`.
5. Проверить AGENTS.md: новая сессия должна «знать» стек и правило «не читать app.py целиком» без подсказки.
6. Имитировать длинную сессию: убедиться, что compaction срабатывает на ~75% (видно по summary в истории) и `/compact` работает вручную.
7. Тесты: `python -m pytest` проходит.

## Риски и митигация
- **Неверный ID модели** в 1.2: если `kilo/zai-coding/glm-5.2` не резолвится — взять точную строку из `/models`. Митигация: проверка на шаге валидации 1.
- **`memory` MCP реально используется** в других проектах: отключаем через `enabled:false` в `kilo.jsonc` (глобально) — если нужен в конкретном проекте, там поднять `enabled:true` в проектном конфиге. Non-destructive: ключи/настройки сохранены.
- **KCP-плагин не запускается** (несовместимая версия `@kilocode/plugin`): откатить через удаление строки `"plugin"` из `.kilo/kilo.jsonc`; экономия CLI остаётся частично через правила AGENTS.md.
- **`threshold_percent:75` + `tail_turns:3`** повышают расход на summarization: если стоимость важнее связности — вернуть `threshold_percent` в unset и `tail_turns:2`.

## Вне scope
- Перенос секретов в SQLite/Postgres (см. действующий roadmap MVP-security).
- Полная перенастройка глобальных агентов (оставлены как есть).
- Внедрение ruff/mypy (отдельная задача; пока `py_compile` + pytest).

## Передавается реализации
Все шаги требуют правок конфигов (`~/.config/kilo/kilo.jsonc`, `.kilo/kilo.jsonc`), `npm install` в `.kilo/`, и создания `AGENTS.md` — это работа для implementation-агента (планировщик исходники не трогает).
