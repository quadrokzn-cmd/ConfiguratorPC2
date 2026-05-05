# Автоматическая загрузка прайсов поставщиков

Этап 12.3 открыл блок 12.x — ежедневное автообновление прайсов от
шести поставщиков без участия менеджера. До 12.x загрузка была только
ручной (`/admin/price-uploads`, этап 11.2).

## Архитектура

```
┌──────────────────────┐   04:00 МСК   ┌──────────────────────────┐
│ portal/scheduler.py  │ ────────────▶ │ run_auto_load(slug)       │
│ APScheduler cron     │               │ app/services/auto_price/  │
└──────────────────────┘               └────────────┬──────────────┘
                                                    │
                                                    ▼
┌──────────────────────────────┐   fetch_and_save() │
│ Fetcher по каналу:            │ ◀──────────────────┘
│   • TreolanFetcher (REST API) │
│   • OcsFetcher (IMAP, 12.1)   │  ← TODO
│   • NetlabFetcher (URL, 12.2) │  ← TODO
│   • …                         │  ← TODO
└──────────────┬───────────────┘
               │ PriceRow[]
               ▼
┌─────────────────────────────────────────────────────┐
│ orchestrator.save_price_rows() — общий save-pipeline │
│   • _get_or_create_supplier                          │
│   • upsert supplier_prices                           │
│   • mapping (MPN/GTIN/NLU)                           │
│   • disappeared-детекция                             │
│   • запись price_uploads + report_json               │
└─────────────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────┐
│ run_auto_load обновляет:             │
│   auto_price_loads (state)           │
│   auto_price_load_runs (журнал)      │
│   Sentry — при ошибке                │
└──────────────────────────────────────┘
```

## Таблицы

### `auto_price_loads`
Текущее состояние подключения по каждому из 6 поставщиков. Одна строка
на slug.

| Колонка | Назначение |
|--|--|
| `supplier_slug` | UNIQUE, совпадает с ключом из `LOADERS` (`treolan`, `ocs`, …) |
| `enabled` | флаг «включить в ежедневный обход APScheduler-а» |
| `status` | `idle` / `running` / `success` / `error` |
| `last_run_at`, `last_success_at`, `last_error_at` | timestamp последних событий |
| `last_error_message` | текст последней ошибки (TEXT, truncate 2000) |
| `last_price_upload_id` | FK → `price_uploads.id`, ON DELETE SET NULL |

### `auto_price_load_runs`
Журнал каждого запуска (manual/scheduled). Используется для UI «Журнал
запусков» на `/admin/auto-price-loads`.

| Колонка | Назначение |
|--|--|
| `supplier_slug` | какой поставщик |
| `started_at`, `finished_at` | время старта и финиша |
| `status` | `running` / `success` / `error` |
| `error_message` | (только при error) первые 2000 символов |
| `price_upload_id` | FK → `price_uploads.id` |
| `triggered_by` | `manual` / `scheduled` |

## Подключение нового поставщика

1. Создать `app/services/auto_price/fetchers/<slug>.py`:

   ```python
   from app.services.auto_price.base import BaseAutoFetcher, register_fetcher

   @register_fetcher
   class MyFetcher(BaseAutoFetcher):
       supplier_slug = "ocs"

       def fetch_and_save(self) -> int:
           # 1. читать env (свои ключи), валидировать;
           # 2. дергать канал (IMAP/HTTP/…);
           # 3. собрать PriceRow[];
           # 4. вернуть id новой записи price_uploads через
           #    orchestrator.save_price_rows().
   ```

2. Импорт нового файла в `app/services/auto_price/__init__.py`,
   чтобы `@register_fetcher` сработал на старте.

3. Добавить env-переменные в `.env.example` и в Railway (portal-сервис).

4. Накатить **только** код — миграции 028 уже хватает; новые таблицы не
   нужны.

5. UI `/admin/auto-price-loads` автоматически получит поддержку нового
   slug'а: будет активна кнопка «Запустить» и переключатель «Авто».

## Расписание APScheduler

В `portal/scheduler.py` зарегистрирован cron-job `auto_price_loads_daily`:

- **04:00 МСК** ежедневно (после daily_backup в 03:00 МСК).
- Обходит все строки `auto_price_loads` с `enabled = TRUE`.
- На каждую вызывает `run_auto_load(slug, triggered_by='scheduled')`.
- Ошибка одного поставщика не прерывает остальных.

Активация — под тем же флагом `RUN_BACKUP_SCHEDULER=1` или
`APP_ENV=production`. На локалке/в pytest без флагов задача не
регистрируется.

## Throttle ручных запусков

В `runner.run_auto_load` есть защита `MANUAL_THROTTLE_SECONDS = 300`:
если `triggered_by='manual'` и `last_run_at` был меньше пяти минут назад
— бросается `TooFrequentRunError`. UI ловит и показывает 429 +
flash-сообщение. Для `scheduled` throttle игнорируется.

## Реализованные каналы (на 12.3)

| Поставщик | Канал | Статус |
|--|--|--|
| Treolan | REST API + JWT (`/v1/auth/token`, `/v1/Catalog/Get`) | ✅ 12.3 |
| OCS | IMAP (письма с прикреплённым XLS) | ⏳ 12.1 |
| Merlion | IMAP / прямой URL | ⏳ 12.2 |
| Netlab | прямой URL | ⏳ 12.4 |
| Ресурс Медиа | — | ⏳ 12.4 |
| Green Place | — | ⏳ 12.4 |

## Treolan API: ключевые поля ответа

`POST /v1/Catalog/Get` возвращает:

```json
{
  "categories": [{"id": 100, "rusName": "Комплектующие->Процессоры"}, …],
  "positions":  [{
      "articul":      "BX8071512400F",
      "rusName":      "Процессор Intel Core i5-12400F BOX",
      "vendor":       "Intel",
      "currentPrice": "180.50",   ← приоритет; если 0 — fallback на price
      "price":        "200.00",
      "currency":     "USD",      ← USD|RUB; конвертация в RUB через ЦБ
      "atStock":      "12",
      "inTransit":    "0",
      "gtin":         "5032037240306",
      "category-id":  100
  }, …]
}
```

## Env-переменные (12.3)

```
TREOLAN_API_BASE_URL=https://api.treolan.ru/api    # default; можно опустить
TREOLAN_API_LOGIN=<выдаёт Treolan>
TREOLAN_API_PASSWORD=<выдаёт Treolan>
```

Без `TREOLAN_API_LOGIN`/`TREOLAN_API_PASSWORD` `TreolanFetcher.__init__`
бросает `RuntimeError` со списком ожидаемых переменных — это безопасно:
APScheduler обходит каждого поставщика отдельно и при ошибке Treolan
просто пропускает его, не ломая обход остальных.
