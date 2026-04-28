# Мониторинг ошибок: Sentry (этап 9В.3)

К двум сервисам — порталу и конфигуратору — подключён Sentry SDK,
чтобы 5xx-ошибки и неперехваченные исключения видеть в одном месте,
а не вылавливать руками из логов Railway.

## Зачем

До 9В.3 единственный способ заметить «что-то сломалось у менеджера в
Конфигураторе» — это либо чтобы менеджер написал в чат, либо чтобы
кто-то заглянул в Railway logs. На сценарии «отвалилась миграция /
зашёл в продакшн с битой фичей» — это слишком медленно. Sentry даёт:

- автоматический stack trace при каждом 5xx;
- группировку повторяющихся ошибок;
- email-нотификацию (опционально, настраивается в Sentry UI);
- привязку события к конкретному пользователю (через `set_user`).

## Архитектура

```
shared/sentry_init.py            ← общий init (init_sentry, before_send, mask_dsn)
app/main.py                      ← init_sentry("configurator") сразу после load_dotenv()
portal/main.py                   ← init_sentry("portal")        сразу после load_dotenv()
shared/auth.py: current_user()   ← sentry_sdk.set_user({id, login}) после идентификации
portal/routers/admin_diagnostics.py   ← /admin/sentry-test, /admin/sentry-message
```

`init_sentry(service_name)` — синхронная функция. Если `SENTRY_DSN` не
задан, возвращает `False` и пишет в лог одну строку
`"Sentry disabled (no SENTRY_DSN) for <service>"` — так локалка и
тесты не зависят от наличия DSN.

## Два проекта в Sentry

В Sentry-аккаунте создаём два отдельных проекта (Python + FastAPI):
`kvadro-tech-portal` и `kvadro-tech-configurator`. У каждого свой DSN.

В Railway прописываем env-переменные:

| Сервис в Railway | Переменная             | Значение                          |
|------------------|------------------------|-----------------------------------|
| portal           | `SENTRY_DSN_PORTAL`        | DSN из проекта portal             |
| configurator     | `SENTRY_DSN_CONFIGURATOR`  | DSN из проекта configurator       |

Можно вместо двух per-service переменных задать одну общую `SENTRY_DSN`
(тогда оба сервиса польются в один проект) — `init_sentry` сначала
проверяет per-service переменную, потом общую. Per-service удобнее:
дашборды, алерты и квоты не пересекаются.

## Что отправляется в Sentry

Отправляется:

- **5xx** (RuntimeError, неперехваченные исключения, HTTPException 500).
- **ERROR-логи** (`logger.error(...)`, в т.ч. `logger.exception(...)`).
- **transactions** на 10% запросов (для performance-метрик).

Не отправляется (отфильтровано в `before_send`):

- **HTTPException 4xx** (401, 403, 404, валидация) — это пользовательские
  ошибки, не баги.
- **`asyncio.CancelledError`** — нормальное поведение FastAPI/uvicorn
  на shutdown'е и таймаутах, не бот.

Дополнительно:

- `traces_sample_rate=0.1` — 10% запросов попадают в performance-трекинг.
- `/healthz` отдельно занижен до `0.01` (1%) через `traces_sampler` —
  его бьёт Railway каждые 30 секунд, забивать им квоту нельзя.
- `send_default_pii=False` — IP, cookies и заголовки не попадают в
  Sentry автоматически. Привязка пользователя — только `id + login`.

## Тестовые endpoint'ы

Только в портале, под `require_admin`. Пригодятся когда нужно убедиться,
что DSN в Railway правильный, и события реально доходят до Sentry-проекта.

### `GET /admin/sentry-test`

Бросает `RuntimeError("Sentry test exception from /admin/sentry-test")` —
FastAPI возвращает 500, Sentry ловит исключение через FastAPI-интеграцию
и шлёт событие в проект `portal`.

Используется один раз после деплоя, чтобы убедиться что всё работает.

### `GET /admin/sentry-message`

Шлёт `sentry_sdk.capture_message("Sentry test message", level="info")` и
возвращает `{"status": "sent"}`. Не бросает 500-ку — удобнее когда не
хочется захламлять Railway logs.

⚠️ **Не дёргать локально без явной необходимости**: если в локальном
`.env` стоит prod-DSN, событие уйдёт в боевой проект Sentry. Безопаснее
проверять оба endpoint'а только через Chrome после деплоя.

В конфигураторе аналогичных endpoint'ов нет — добавим, если будет нужно
проверить связку конфигуратор↔Sentry отдельно.

## Как читать события в Sentry UI

1. Заходим в [sentry.io](https://sentry.io), выбираем проект.
2. Раздел **Issues** — сгруппированные ошибки.
3. У каждого события — stack trace, breadcrumb'ы (последние логи и
   запросы перед ошибкой), `tags.service`, привязанный пользователь.
4. Раздел **Performance** — медленные транзакции (но т.к. sample_rate
   маленький, статистики мало; для серьёзного APM ставим выше отдельно).

Алерты — `Settings → Alerts → Create Alert`. Минимальный набор:
«новая issue», «issue повторилась >10 раз за час».

## Лимиты и квота

Developer-план Sentry — **5 000 errors** и **10 000 transactions** в
месяц **на аккаунт** (не на проект). При нашей нагрузке это с большим
запасом, но если что-то начнёт фонтанировать ошибками — квота кончится
быстро. Если такое произойдёт, в первую очередь — починить баг; во
вторую — занизить `traces_sample_rate` или временно отключить Sentry
(см. ниже).

## Как временно отключить Sentry

Самый быстрый способ: удалить `SENTRY_DSN` (или `SENTRY_DSN_PORTAL`/
`SENTRY_DSN_CONFIGURATOR`) из Railway env vars и редеплоить сервис.
`init_sentry` увидит пустой DSN, вернёт `False`, SDK не поднимется —
сервис продолжит работать как раньше, ошибки просто не будут уходить в
Sentry.

## Локально

Локально Sentry **не нужен**. `.env` без `SENTRY_DSN` → init возвращает
False, в логе одна INFO-строка. Если хочется на минуту включить — можно
зарегистрировать тестовый проект в личном Sentry-аккаунте и положить
его DSN в `.env`. Но боевые DSN (`SENTRY_DSN_PORTAL`,
`SENTRY_DSN_CONFIGURATOR`) на локалку **не клади**: каждый локальный
запуск будет фоном слать events в прод-проект.
