# 2026-05-13 — fallback DATABASE_URL → DATABASE_PUBLIC_URL в shared/config.py

## 1. Какая задача была поставлена

Закрыть backlog #17 (`plans/2026-04-23-platforma-i-aukciony.md`) — расширить
`shared/config.py`, чтобы при отсутствии `DATABASE_URL` использовался
`DATABASE_PUBLIC_URL`. На prod-backfill 2026-05-13 (чат #4) `write_audit`
падал из-за расхождения между именем ENV-переменной в prod-env-файле
(`DATABASE_PUBLIC_URL`) и тем, что ожидает `shared.config` (`DATABASE_URL`);
исполнитель обходил это прямым INSERT в `audit_log`. Поскольку параллельно
работал чат `9a-fixes-3 reparse` с той же prod-env-конфигурацией —
правка нужна была срочно, чтобы он смог подобрать fallback после merge.

## 2. Как я её решал

1. **Worktree** `feature/config-database-url-fallback` отделил параллельные
   зоны (тот чат — данные на БД, я — код).
2. **Discovery** `shared/config.py`: `database_url` читался через
   `_require_env("DATABASE_URL")` (lambda в `default_factory` поля
   dataclass'а). Потребители `settings.database_url` — только
   `shared/db.py` (`create_engine`) и тесты; изменение прозрачное.
   Существующих тестов под `shared/config` не было — папка
   `tests/test_shared/` живёт под другие модули.
3. **Реализация:** новый хелпер `_resolve_database_url()` рядом с
   `_resolve_session_secret` (по стилю файла — отдельная функция,
   logger.info при срабатывании fallback'а, RuntimeError с описанием
   обеих переменных, если ни одной нет). Поле `database_url` указывает
   на хелпер; имя поля и внешний API класса не меняются.
4. **Тесты:** `tests/test_shared/test_config.py` — 5 кейсов через
   `monkeypatch.setenv`/`delenv` + `caplog` для проверки, что INFO-лог
   сработал и URL в нём не светится. Тесты идут на helper напрямую,
   а не через инстанцирование `Settings()` — глобальный `settings`
   живёт модулем и его перестройка чувствительна к conftest-цепочке.
5. **Прогоны:** target-файл (5 passed за 7 сек) → полный pytest
   (1993 passed, 4 skipped, 117 сек). Baseline до правки — 1988 passed,
   дельта +5 новых тестов.
6. **План + рефлексия:** strikethrough на #17 в backlog, новый
   мини-этап в конце плана со ссылкой на этот файл.

## 3. Решил ли — да / нет / частично

**Да, полностью.** Все 5 кейсов DoD покрыты тестами:

- Только `DATABASE_URL` → используется он.
- Только `DATABASE_PUBLIC_URL` → fallback + INFO-лог без значения.
- Пустая/whitespace `DATABASE_URL` → fallback.
- Оба заданы → primary `DATABASE_URL`.
- Ни одного → RuntimeError, message содержит «DATABASE_URL».

Полный pytest зелёный (1993 passed). Worktree вольётся в master через
rebase + ff-only merge (см. шаг финального commit'а).

## 4. Эффективно ли решение, что можно было лучше

**Эффективно:**

- Изоляция через worktree оказалась дешёвой — параллельный чат на
  данных не конфликтовал ни по файлам, ни по веткам. Конфликт по
  `plans/2026-04-23-platforma-i-aukciony.md` ожидался при rebase
  (оба чата добавляют свой мини-этап), но это управляемо в merge-tool'е.
- Helper-функция вместо лямбды — в стиле соседних `_resolve_session_secret`
  и `_resolve_cookie_domain`. Минимальная правка без рефакторинга
  соседнего кода.
- Тестирование helper'а напрямую, минуя `Settings()` — позволило
  не воевать с глобальным синглтоном `settings = Settings()` в конце
  модуля и conftest-цепочкой, которая выставляет `DATABASE_URL` до
  первого импорта.

**Что можно было лучше:**

- Можно было параллельно (одним сообщением Bash) запустить discovery и
  baseline-pytest, чтобы baseline-цифра считалась пока я делаю Edit'ы.
  Не критично — пакет тестов 117 сек, всё равно ждать после правок.
- `.env.example` не тронул, как было оговорено в инструкции. Но при
  следующем визите файла стоит добавить однострочный комментарий про
  fallback — это снимет вопрос «куда писать prod-DSN при подключении
  снаружи».

## 5. Как было и как стало

**Было** (`shared/config.py`):

```python
database_url: str = field(default_factory=lambda: _require_env("DATABASE_URL"))
```

Если в окружении только `DATABASE_PUBLIC_URL` (типичный prod-env-файл
для подключения снаружи к Railway), Settings падает с RuntimeError
«DATABASE_URL не задана». В чате #4 на prod-backfill пришлось обходить
это через прямой `INSERT INTO audit_log` без shared.audit.

**Стало:**

```python
database_url: str = field(default_factory=_resolve_database_url)
```

где `_resolve_database_url()` пробует `DATABASE_URL`, при пусто/отсутствует
переключается на `DATABASE_PUBLIC_URL` с INFO-логом «database_url
fallback: using DATABASE_PUBLIC_URL» (только факт, без значения).
Если нет ни одной — RuntimeError с описанием обеих переменных. Имя
поля Settings и внешний API не меняются.

Backlog #17 закрыт, чат `9a-fixes-3 reparse` после merge сможет
работать с prod-env-файлом без обходов.
