# Аудит-лог действий пользователей (Этап 9В.4)

Внутренний журнал значимых действий менеджеров и админов: входы в систему,
создание/удаление проектов, экспорт КП, отправка писем поставщикам,
изменения ролей и прав. Зачем:

- **Compliance** для B2B-проекта — отвечать на вопросы вроде «кто отправил
  письмо поставщику X в марте».
- **Расследование инцидентов** — «кто удалил проект 47».
- **Прозрачность для команды** — видно свою активность и активность
  других админов.

Sentry собирает **ошибки**, аудит-лог фиксирует **нормальные действия**.
См. также [monitoring.md](monitoring.md).

## Где смотреть

`https://app.quadro.tatar/admin/audit` — для роли `admin`. Менеджеры
страницу не видят (403).

UI:
- Фильтры: пользователь, действие, тип цели, сервис, диапазон дат МСК.
- Таблица: время МСК, пользователь, действие, цель, IP, JSON-payload.
- Пагинация по 50 записей.
- Кнопка «Экспорт CSV» — отдаёт текущую выборку фильтров полным дампом
  (без пагинации). UTF-8 + BOM, чтобы Excel корректно открывал.

## Что фиксируем

Все константы — в [`shared/audit_actions.py`](../shared/audit_actions.py).
Текущий список:

| Категория      | Action                       | Когда пишется                                      |
|----------------|------------------------------|----------------------------------------------------|
| Аутентификация | `auth.login.success`         | Успешный вход в портал                             |
|                | `auth.login.failed`          | Неверный логин/пароль (ловит и брутфорс)           |
|                | `auth.logout`                | Выход из системы (GET и POST)                      |
| Пользователи   | `user.create`                | Создание учётки в `/admin/users`                   |
|                | `user.toggle_active`         | Активация/деактивация                              |
|                | `user.role_change`           | Смена роли admin/manager (`from`/`to` в payload)   |
|                | `user.permission_change`     | Перезапись permissions JSONB                       |
| Проекты        | `project.create`             | Создание проекта (через `/query` или `POST /projects`) |
|                | `project.update`             | Переименование                                     |
|                | `project.delete`             | Удаление                                           |
| Сборки         | `build.create`               | Успешная генерация конфигурации NLU                |
|                | `build.reoptimize`           | Полный пересбор спецификации                       |
| Экспорт        | `export.excel`               | Скачан Excel проекта                               |
|                | `export.kp_word`             | Скачан Word/КП                                     |
| Поставщики     | `supplier.email_sent`        | Письмо поставщику успешно ушло                     |
| Компоненты     | `component.hide`             | Компонент скрыт из подбора                         |
|                | `component.show`             | Компонент возвращён в подбор                       |
|                | `component.update`           | Ручная правка характеристик                        |
| Бекапы         | `backup.manual_run`          | Ручной запуск бекапа БД                            |
|                | `backup.download`            | Скачивание дампа из B2                             |
| Аудит-лог сам  | `audit.view`                 | Открытие `/admin/audit` (с фильтрами в payload)    |

## Что НЕ фиксируем

Сознательно НЕ пишем:
- GET-запросы на просмотр страниц (главная, список проектов, карточка
  проекта, история).
- Чтение списков (`/admin/users` GET, `/admin/queries` GET и т.п.).
- Healthcheck `/healthz`.
- Любые middleware-перехваты (permission denied, redirect-to-login).

Иначе таблица распухнет до миллионов строк за месяц, а полезный сигнал
утонет в шуме.

## Структура таблицы

Миграция `migrations/018_audit_log.sql`:

```sql
CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_login    TEXT,
    action        TEXT NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip            INET,
    user_agent    TEXT,
    service       TEXT NOT NULL  -- 'portal' / 'configurator'
);
```

Особенности:

- `user_id ON DELETE SET NULL`, а не `CASCADE`. Если пользователя удалят,
  его действия в логе остаются. Имя сохраняется в `user_login` (намеренная
  денормализация).
- `target_id` — `TEXT`, чтобы поддержать разные типы первичных ключей
  (где-то int, где-то составной).
- `service` — `'portal'` или `'configurator'`. Один и тот же action может
  прийти из обоих (например, `project.create`).
- Индексы — на `created_at DESC` (выборка последних), `user_id`,
  `action`, `(target_type, target_id)`.

## Ретенция

Записи старше **180 дней** удаляются раз в неделю. Реализация:
APScheduler-задача `audit_retention` в [`portal/scheduler.py`](../portal/scheduler.py),
расписание — каждое воскресенье в 04:00 МСК (после ежедневного бекапа в
03:00, чтобы удалённые строки попали в воскресный weekly-снимок B2).

Конфигурация:
- `AUDIT_RETENTION_DAYS` — переопределение значения 180 (минимум 1).
- Под тем же флагом, что и бекапы: запускается при `APP_ENV=production`
  или явном `RUN_BACKUP_SCHEDULER=1`. На локалке и в pytest задача
  не активируется.

Если нужны записи старше 180 дней — берутся из бекапов B2.

## Как добавить новый action

Паттерн на будущие этапы:

1. Добавить константу в [`shared/audit_actions.py`](../shared/audit_actions.py)
   в подходящую секцию.
2. В роутере импортировать константу + `write_audit` + `extract_request_meta`.
3. Вызвать `write_audit(...)` **после** успешного выполнения основного
   действия (после `db.commit()`) — но не до. Если действие не
   выполнилось, в аудит писать нечего.
4. В `payload` класть только важный контекст (id, имя, diff). Не клади
   полные тела запросов — это утопит UI и раздует БД.
5. Убедиться, что `write_audit` обёрнут try/except внутри (он сам это
   делает на уровне модуля; в роутере дополнительная обёртка не нужна).

Пример — добавление аудита переименования проекта:

```python
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import ACTION_PROJECT_UPDATE

ip, ua = extract_request_meta(request)
spec_service.rename_project(db, project_id=project_id, name=clean)
write_audit(
    action=ACTION_PROJECT_UPDATE,
    service="configurator",
    user_id=user.id,
    user_login=user.login,
    target_type="project",
    target_id=project_id,
    payload={"name": clean},
    ip=ip,
    user_agent=ua,
)
```

## Безопасность

- **Пароли никогда не попадают в payload.** Даже маской `***` —
  не пиши вообще. При сбросе пароля админом фиксируется только сам
  факт (`user.password_reset`), без значений.
- **Полные тела запросов не логируются автоматически.** В payload идёт
  только то, что явно перечислил автор интеграции.
- **IP — реальный клиентский.** `extract_request_meta` берёт первый IP
  из `X-Forwarded-For` (Railway его прокидывает), фолбэк — `request.client.host`.
- **User-Agent обрезается до 500 символов** — длинные UA-строки бывают,
  но смысла хранить их полностью нет.

## Тестовый режим

Переменная окружения `AUDIT_DISABLED=1` отключает запись:
`write_audit()` становится no-op'ом. Используется в тестовых фикстурах,
которые работают без БД (юнит-тесты NLU и т.п.). В обычной dev-среде
и на проде эту переменную не выставляй.

## Отказоустойчивость

`write_audit` оборачивает весь INSERT в try/except и при любой ошибке
БД пишет `WARNING` (не `ERROR` — иначе Sentry будет шуметь, см.
`event_level=ERROR` в `LoggingIntegration`) и продолжает. Принцип:

> Аудит важен, но он НЕ должен ломать пользовательский запрос.

Если аудит-запись не сохранилась (внешняя сетевая ошибка к Postgres,
например), пользователь свой запрос пройдёт штатно. Что произошло —
видно в логах Railway по WARNING-строке.
