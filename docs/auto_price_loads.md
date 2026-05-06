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
┌──────────────────────────────────┐   fetch_and_save() │
│ Fetcher по каналу:                │ ◀──────────────────┘
│   • TreolanFetcher (REST API)     │  ✅ 12.3
│   • OCSImapFetcher (IMAP)         │  ✅ 12.1
│   • MerlionImapFetcher (IMAP+ZIP) │  ✅ 12.1
│   • NetlabHttpFetcher (HTTP+ZIP)  │  ✅ 12.2
│   • ResursMediaFetcher (?)        │  ⏳ 12.4
│   • GreenPlaceFetcher (?)         │  ⏳ 12.4
└──────────────┬───────────────────┘
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

После 12.2 в `portal/scheduler.py` каждый поставщик имеет свой
персональный cron-job с 10-минутным шагом — все в утренние часы, чтобы
к началу рабочего дня свежие прайсы уже лежали в БД.

| ID | Время МСК | Канал | Поставщик |
|--|--|--|--|
| `auto_price_loads_treolan`      | 07:00 | REST API              | Treolan |
| `auto_price_loads_ocs`          | 07:10 | IMAP                  | OCS |
| `auto_price_loads_merlion`      | 07:20 | IMAP                  | Merlion |
| `auto_price_loads_netlab`       | 07:30 | HTTP (прямая ссылка)  | Netlab |
| `auto_price_loads_resurs_media` | 07:40 | — (12.4)              | Ресурс Медиа |
| `auto_price_loads_green_place`  | 07:50 | — (12.4)              | Green Place |

Каждый job:

1. Читает `auto_price_loads.enabled` для своего slug. Если **FALSE** —
   тихо выходит (тумблер выключен пользователем, никаких записей в
   журнал).
2. Иначе вызывает `run_auto_load(slug, triggered_by='scheduled')`. Если
   fetcher ещё не зарегистрирован (resurs_media / green_place до 12.4),
   `run_auto_load` бросит `ValueError` — runner запишет его как `error`
   в `auto_price_load_runs`. Это допустимое поведение: пока тумблер OFF
   ничего не происходит; включил, но канала ещё нет — увидишь ошибку
   в журнале.
3. Любая ошибка ловится и пишется в WARN, чтобы не валить
   scheduler-loop.

10-минутный интервал между поставщиками — защита от параллельных
orchestrator-вставок в `supplier_prices` и от одновременных подключений
к IMAP/HTTP. Раньше (до 12.2) задач было всего две (REST в 04:00 и IMAP
в 14:30) — это дробило прайсы по дню; теперь всё к открытию.

Активация — под тем же флагом `RUN_BACKUP_SCHEDULER=1` или
`APP_ENV=production`. На локалке/в pytest без флагов задачи не
регистрируются.

## Throttle ручных запусков

В `runner.run_auto_load` есть защита `MANUAL_THROTTLE_SECONDS = 300`:
если `triggered_by='manual'` и `last_run_at` был меньше пяти минут назад
— бросается `TooFrequentRunError`. UI ловит и показывает 429 +
flash-сообщение. Для `scheduled` throttle игнорируется.

## Реализованные каналы (на 12.2)

| Поставщик | Канал | Статус |
|--|--|--|
| Treolan | REST API + JWT (`/v1/auth/token`, `/v1/Catalog/Get`) | ✅ 12.3 |
| OCS | IMAP (XLSX вложение, Subject «B2B OCS — Состояние склада и цены») | ✅ 12.1 |
| Merlion | IMAP (ZIP с XLSX, Subject «Прайс-лист MERLION», forward через Gmail) | ✅ 12.1 |
| Netlab | прямой HTTP-URL (ZIP с DealerD.xlsx, без авторизации) | ✅ 12.2 |
| Ресурс Медиа | — | ⏳ 12.4 |
| Green Place | — | ⏳ 12.4 |

## IMAP-канал (12.1)

### Архитектура

```
BaseImapFetcher (общий каркас, fetchers/base_imap.py)
├── OCSImapFetcher     — XLSX вложение
└── MerlionImapFetcher — ZIP → распаковка → XLSX
```

`fetch_and_save()` любого IMAP-fetcher'а:

1. Открывает IMAP/SSL-соединение (host/port/SSL — из ENV),
   логин по `IMAP_USER`/`IMAP_PASSWORD` (fallback `SMTP_USER`/`SMTP_APP_PASSWORD`).
2. `INBOX → SEARCH SINCE` за `search_window_days` (=14). Окно намеренно
   широкое — покрывает выходные, праздники, двухнедельные простои.
3. Клиентский фильтр по `sender_pattern` (regex по From / Reply-To /
   X-Forwarded-For / Return-Path / Sender — Merlion ходит через
   Gmail-forward, реальный домен может оказаться в любом из них) и
   `subject_pattern`.
4. Идемпотентность по `Message-ID`: перед обработкой смотрим
   `auto_price_load_runs.source_ref` за последние 30 дней (по slug'у);
   если письмо уже обработано — пропускаем.
5. Берём самое свежее необработанное по `Date` → извлекаем первое
   attachment с подходящим расширением → проверяем размер (≤50 МБ).
6. Подкласс делает `parse_attachment(bytes, filename) → List[PriceRow]`:
   - **OCS**: записывает bytes во временный `.xlsx` и зовёт `OcsLoader.iter_rows`.
   - **Merlion**: распаковывает ZIP, ищет все `.xlsx` рекурсивно,
     берёт самый большой и зовёт `MerlionLoader.iter_rows`.
7. Зовёт `orchestrator.save_price_rows()` — общий save-pipeline
   (тот же, что и `/admin/price-uploads`).

### Идемпотентность и `source_ref`

Миграция 029 добавила колонку `auto_price_load_runs.source_ref TEXT`
плюс частичный индекс `(supplier_slug, source_ref) WHERE source_ref IS NOT NULL`.

- Для IMAP-канала: после успешной обработки runner кладёт `Message-ID`
  письма в `source_ref`. При следующем запуске тот же Message-ID
  отфильтровывается за 30 дней.
- Для REST-канала (Treolan): остаётся NULL — идемпотентность
  обеспечена самим `Catalog/Get` без срезов времени.

### `NoNewDataException` и статус `no_new_data`

Если в окне нет нового письма (или все уже обработаны), fetcher бросает
`NoNewDataException`. Runner ловит её **отдельно** от обычных ошибок:

- `auto_price_load_runs.status = 'no_new_data'`, `error_message` = текст
  исключения, `source_ref = NULL`, `finished_at = NOW()`.
- `auto_price_loads.status = 'no_new_data'`, `last_run_at = NOW()`,
  `last_success_at` / `last_error_at` **не трогаются**, `last_error_message`
  очищается (это не ошибка).
- **`orchestrator` НЕ вызывается** — пустой `rows` обнулил бы остатки
  через disappeared-логику. Это ключевая защита параллельно с
  `total_rows == 0 → failed` в `orchestrator._record_upload`.

В UI `/admin/auto-price-loads` `no_new_data` рендерится как
yellow-badge «нет новых писем» (не error / red, не success / green).

### Subject / sender паттерны

| Поставщик | sender (regex) | subject (regex) | Вложение |
|--|--|--|--|
| OCS | `@ocs\.ru\b` | `^\s*B2B\s+OCS\s*-\s*Состояние\s+склада\s+и\s+цены` | `.xlsx`/`.xls` |
| Merlion | `@merlion\.ru\b` | `^\s*Прайс-лист\s+MERLION` | `.zip` (внутри `.xlsx`) |

Регексы проверяются case-insensitive.

### Env-переменные (12.1)

```
# По умолчанию IMAP-канал использует SMTP_USER / SMTP_APP_PASSWORD
# (VK Workspace выдаёт общий app password для SMTP и IMAP). Дополнительные
# переменные нужны ТОЛЬКО если IMAP-креды разойдутся со SMTP:
IMAP_HOST=imap.mail.ru   # default
IMAP_PORT=993            # default
IMAP_USE_SSL=true        # default
IMAP_USER=               # fallback на SMTP_USER
IMAP_PASSWORD=           # fallback на SMTP_APP_PASSWORD
```

Без `IMAP_USER`/`SMTP_USER` (и пары паролей) `_read_imap_credentials()`
бросает `RuntimeError` со списком ожидаемых переменных — runner поймает
её как обычную ошибку, выставит `status='error'`.

## HTTP-канал Netlab (12.2)

Netlab публикует актуальный дилерский прайс по прямой публичной ссылке
без авторизации. Канал реализован как `NetlabHttpFetcher` в
`app/services/auto_price/fetchers/netlab_http.py`.

### Поток

1. `httpx.Client.get(NETLAB_PRICE_URL)` с `follow_redirects=True`,
   timeout 120с на чтение, 30с на коннект.
2. Retry 3 попытки с backoff 5/15/45 на `httpx.RequestError` и 5xx.
   На 4xx — сразу `RuntimeError` (клиентская ошибка, не временная).
3. Sanity-check размера: `Content-Length` (если есть) и фактическая
   длина тела ≤ **50 МБ**. Превышение — `RuntimeError`.
4. Имя файла: сначала из `Content-Disposition` (RFC 6266), при
   отсутствии — basename URL'а.
5. Bytes пишутся во временный `.zip`, путь отдаётся
   `NetlabLoader.iter_rows(filepath)`. Loader сам распакует архив через
   `_open_workbook` и почистит распакованный xlsx-каталог в `finally`.
6. `List[PriceRow]` идёт в общий `save_price_rows()` — тот же путь,
   что и у IMAP/REST-каналов и `/admin/price-uploads`.
7. Временный `.zip` удаляется в `finally`, даже если loader бросил
   исключение.

### Идемпотентность

В отличие от IMAP-канала здесь нет `Message-ID`. `source_ref` остаётся
NULL — каждый скачанный архив рассматривается как «свежий». Это
безопасно: при `total_rows == 0` orchestrator закрывается `failed`
(disappeared не запускается); при ненулевом `rows` — обычный
price-upload, идентичный ручной загрузке через UI.

### Env-переменные (12.2)

```
NETLAB_PRICE_URL=http://www.netlab.ru/products/dealerd.zip   # default в коде
```

`NETLAB_PRICE_URL` опционален: если переменная не задана, fetcher
использует встроенный дефолт (публичную дилерскую ссылку). Дополнительно
переопределять в Railway не требуется. Если когда-нибудь Netlab сменит
URL — выставите эту переменную, и fetcher переключится без релиза.

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

## Развёртывание / эксплуатация

### Railway: всегда указывать `--service portal`

Все Railway-команды для проверки/диагностики Treolan-канала
**ОБЯЗАТЕЛЬНО** запускаются с флагом `--service portal`:

```
railway run --service portal python scripts/...
railway ssh --service portal -- ...
railway variables --service portal
```

Без `--service portal` CLI смотрит в дефолтный сервис проекта
(`ConfiguratorPC2`), где `TREOLAN_API_LOGIN`/`TREOLAN_API_PASSWORD`/
`TREOLAN_API_BASE_URL` не заданы, и любая диагностика покажет ложные
«креды не настроены». Реальные креды живут в env'е именно сервиса
`portal` — там же, где работает APScheduler и `runner.run_auto_load`.

Типовой ручной прогон Treolan на проде:

```
railway ssh --service portal -- python -c "from app.services.auto_price.runner import run_auto_load; print(run_auto_load('treolan', triggered_by='manual'))"
```

или через UI: `POST /admin/auto-price-loads/treolan/run`.
