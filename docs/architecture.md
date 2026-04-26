# Архитектура проекта

Карта основных модулей и их зон ответственности. Для конкретных файлов
смотрите код — этот документ держит **верхнеуровневую модель**, которая
полезна на старте новой задачи или при онбординге.

## Структура репозитория

```
ConfiguratorPC2/
├── app/                ← всё приложение FastAPI
│   ├── main.py         ← точка входа, SessionMiddleware, регистрация роутеров
│   ├── auth.py         ← bcrypt, сессии, current_user / require_login / require_admin
│   ├── config.py       ← настройки из .env
│   ├── database.py     ← engine, SessionLocal, get_db
│   ├── routers/        ← FastAPI-роутеры (auth, main, project, admin, ...)
│   ├── services/       ← бизнес-логика (см. ниже)
│   └── templates/      ← Jinja2-шаблоны (наследование от base.html)
├── migrations/         ← SQL-миграции 001–016 (применяются по порядку)
├── scripts/            ← CLI-скрипты (загрузка прайсов, бэкфилы, утилиты)
├── tests/              ← pytest, 721 passed + 2 skipped
├── static/             ← фронтенд-ассеты
│   ├── src/            ← исходники (main.css → Tailwind)
│   ├── dist/           ← собранный CSS (коммитится в репо)
│   ├── js/             ← AJAX-клиенты (project.js)
│   └── fonts/inter/    ← локальный шрифт Inter
├── design_references/  ← локальные референсы дизайна (gitignored)
├── docs/               ← техническая документация (этот каталог)
├── business/           ← бизнес-контекст КВАДРО-ТЕХ (см. ../business/INDEX.md)
├── data/               ← локальные прайс-листы (gitignored)
├── logs/               ← логи scheduler (gitignored)
├── package.json        ← Tailwind/PostCSS dev-зависимости (Node для сборки CSS)
├── tailwind.config.js  ← дизайн-токены (цвета, шрифт, тени)
├── requirements.txt    ← Python-зависимости
└── README.md           ← стартовый гайд
```

## Слой `app/services/` — главные модули

### `nlu/` — парсинг свободного текста

Превращает «офисный ПК до 60 тысяч, тихий, с SSD» в структурированный
`BuildRequest` (бюджет, ядро, объём ОЗУ, форм-фактор, наличие GPU и т.п.).

Pipeline:

1. **Regex/parser** — быстрые паттерны (бюджет, явные числа,
   ключевые слова).
2. **Fuzzy lookup** — нечёткое сопоставление формулировок («тихий»,
   «дискретный», «игровой») с инвариантами.
3. **Fallback на OpenAI (gpt-4o-mini)** — когда локальная логика
   не вытащила достаточно сигналов. Контролируется
   `services/budget_guard.py` (дневной лимит расходов).

### `configurator/` — подбор конфигурации

Из `BuildRequest` собирает реальную конфигурацию с проверкой
совместимости и минимизацией цены:

- `candidates.py` — выбор кандидатов по каждой категории (CPU, MB, RAM,
  GPU, storage, case, PSU, cooler) под фильтрами (бюджет, бренд, профиль).
- `compatibility.py` — проверки совместимости (сокет CPU↔motherboard,
  тип ОЗУ, форм-фактор корпуса/MB, мощность БП, охват TDP кулера).
- `prices.py` — выбор минимальной цены по каждому компоненту среди
  активных поставщиков.
- `builder.py` — оркестрация: перебор кандидатов, проверка совместимости,
  возврат N лучших конфигураций.

### `price_loaders/` — загрузка прайс-листов поставщиков

Поддержаны 3 поставщика: **OCS**, **Merlion**, **Treolan**. Каждый со
своим Excel-форматом — парсер вынесен в отдельный модуль:

- `base.py` — `BasePriceLoader` с `detect()` (определяет поставщика по
  имени файла), общие утилиты.
- `ocs.py`, `merlion.py`, `treolan.py` — адаптеры конкретных форматов.
- `matching.py` — сопоставление строк прайса с компонентами БД (по MPN,
  затем по GTIN).
- `orchestrator.py` — общий раннер: читает Excel → парсит → матчит →
  пишет в `supplier_prices` + лог в `price_uploads`. Несмапплённые
  строки попадают в `unmapped_supplier_items` для ручного разбора.
- `candidates.py` — поиск кандидатов сопоставления для админ-страницы
  `/admin/mapping`.

### `enrichment/` — обогащение характеристик компонентов

Заполняет специализированные поля (max_tdp кулера, объём кэша CPU,
скорость DDR и т.п.) из доступных источников: regex по описанию,
парсинг названий, fallback в LLM. См.
[enrichment_techdebt.md](enrichment_techdebt.md) — известные ограничения.

### `export/` — экспорт спецификаций

- **Excel** (openpyxl) — спецификация проекта в табличном виде.
- **Word/КП** (python-docx) — коммерческое предложение. Шрифт **Calibri
  11pt** в Normal-стиле, программная сборка таблицы с явным `rFonts`
  на каждом run (Этап 9А.2.7 — см. [design-decisions.md](design-decisions.md)).
- **Email** — отправка готового КП клиенту.
- `exchange_rate.py` + `scheduler.py` — APScheduler, 5 cron-задач
  обновления курса USD→RUB с ЦБ РФ:
  **8:30, 13:00, 16:00, 17:00, 18:15** МСК.

### Прочие сервисы

- `web_service.py` — бизнес-логика веб-роутов.
- `spec_service.py` / `spec_naming.py` — CRUD проектов и
  автоматическое именование спецификаций (Этап 6.2).
- `web_result_view.py` — обогащение компонентов на отображении (`specs_short`, `raw_specs`).
- `mapping_service.py` — ручное сопоставление `unmapped_supplier_items` (Этап 7).
- `budget_guard.py` — контроль дневного лимита OpenAI.

## Веб-страницы

| URL                       | Назначение                                                     |
|---------------------------|----------------------------------------------------------------|
| `/`                       | Форма нового запроса; создаёт проект + первую конфигурацию     |
| `/projects`               | Список проектов пользователя (админ — все)                     |
| `/project/{id}`           | Детальная страница: конфигурации, чекбоксы «в спецификацию»    |
| `/project/{id}/new_query` | Добавление ещё одной конфигурации в проект                     |
| `/query/{id}`             | Просмотр одной конфигурации                                    |
| `/history`                | Плоская история запросов пользователя                          |
| `/admin/users`            | Управление пользователями                                       |
| `/admin/budget`           | Бюджет OpenAI и расходы                                         |
| `/admin/queries`          | Все запросы (админ)                                             |
| `/admin/mapping`          | Ручное сопоставление поставщиков                                |

AJAX-клиент: `static/js/project.js` (галочки спецификации, количество).
CSRF — заголовок `X-CSRF-Token`.

## Авторизация

- bcrypt-хэш пароля.
- `itsdangerous` + `SessionMiddleware` (cookie-сессии).
- Роли: `admin` и `manager`. Декораторы `require_login` / `require_admin`.
- Админ создаётся через `scripts/create_admin.py` (идемпотентно).
- Менеджеры добавляются через `/admin/users`.

## Поток данных верхнеуровнево

```
Менеджер вводит текст
       ↓
   /query (POST)  →  services/nlu/  →  BuildRequest
                                          ↓
                                services/configurator/
                                          ↓
                                  N конфигураций
                                          ↓
                                Запись в БД (queries, configurations)
                                          ↓
                          Редирект на /project/{pid}?highlight={qid}
                                          ↓
                  Менеджер выбирает чекбоксами → spec_service
                                          ↓
                          services/export/ (Excel / Word / Email)
```

Параллельно: APScheduler каждые ~3 часа обновляет курс ЦБ; цены
в БД хранятся в USD, рубли вычисляются на лету через jinja-фильтры
`to_rub` / `fmt_rub` (см. [design-decisions.md](design-decisions.md)).

## Ссылки на детали

- Запуск проекта, env, миграции — [`../README.md`](../README.md)
- Таблицы и инварианты БД — [database.md](database.md)
- Стек и зависимости — [stack.md](stack.md)
- Почему так, а не иначе — [design-decisions.md](design-decisions.md)
- История и план — [roadmap.md](roadmap.md)
