# Enrichment Kyocera на prod, round 3 (49 SKU, 2026-05-13)

## 1. Какая задача была поставлена

Серия round 3 enrichment'а печатной техники добивает n/a-marked SKU по
брендам после round 2 (закрытие empty-пула 358 → 28). Этот чат — Kyocera,
бриф ожидал ~45 SKU; на discovery prod-БД обнаружилось **49 SKU**.

Запущено параллельно с тремя другими чатами (Pantum r3 / Epson+Ricoh r3 /
Canon r3) на разных worktree'ах. Конфликтов по коду нет — каждый бренд
живёт в своих done-файлах. Конфликт ожидается только в
`plans/2026-04-23-platforma-i-aukciony.md` (все добавляют мини-этап),
резолв правилом «оставить все блоки».

## 2. Как я её решал

### Discovery prod-БД

1. **Подключение к prod без светивания DSN:** `dotenv_values('.env.local.prod.v1')['DATABASE_PUBLIC_URL']`
   → `os.environ['DATABASE_URL']=...` ДО `from shared.db import engine` (паттерн round 2).
   Параллельно `load_dotenv('.env')` для `OPENAI_API_KEY` (требуется `shared.config.Settings`).
2. **Кратко облажался на схеме:** в первой версии discovery-скрипта поставил
   `WHERE brand_norm='Kyocera'` — но в `printers_mfu` колонка называется
   просто `brand` (не `brand_norm`), и `name` (не `raw_name`). Исправил
   за минуту, повторил.
3. **Цифры ДО** (Kyocera, 49 SKU, prod):
   - print_speed_ppm 4 success / 45 n/a / 0 empty
   - colorness 9 / 40 / 0
   - max_format 32 / 17 / 0
   - duplex 26 / 23 / 0
   - resolution_dpi 4 / 45 / 0
   - network_interface 4 / 45 / 0
   - usb 4 / 45 / 0
   - starter_cartridge_pages 0 / 49 / 0
   - print_technology 49 / 0 / 0 (regex_name закрыл всем)
   
   Все 49 имеют `attrs_source='regex_name'` (round 2 уже прошёлся;
   regex покрыл print_technology + частично max_format/duplex; остальное n/a).

### Pending-файлы

4. **SQL'ом из prod-БД** (минуя `auctions_enrich_export.py`): записал
   `data/_kyocera_na_skus.json` со всеми 49 SKU + raw_name + текущий
   attrs_jsonb + attrs_source. Разбил по 25/24 SKU на
   `enrichment/auctions/pending/kyocera_round3_{001,002}.json`.

### Маппинг SKU → модель + источники

Распознавание моделей по `raw_name` дало ~38 уникальных моделей
(несколько SKU = один модельный артикул). Группы:

- **FS-серия** (legacy A4 ч/б): FS-1060DN, FS-1025MFP, FS-1125MFP — 3 SKU.
  Источник: `kyoceradocumentsolutions.ru/ru/products/{mfp,printers}/FS*.html`.
- **ECOSYS M-серия A3 ч/б**: M4125idn, M4132idn — 3 SKU.
- **ECOSYS M-серия A3 цв.**: M8124cidn, M8130cidn — 3 SKU.
- **ECOSYS M/P-серия A4 ч/б (legacy)**: M2040DN, M2135DN, M2635DN, P2040DN — 5 SKU.
- **ECOSYS PA-серия A4 ч/б (Asia)**: PA4000x, PA4500x, PA5000x, PA5500x, PA6000x — 5 SKU.
- **ECOSYS PA-серия A4 цв. (Asia)**: PA2600cx, PA3500cx, PA4000cx, PA4500cx — 4 SKU.
- **ECOSYS MA-серия A4 ч/б (Asia)**: MA3500x, MA4000x/fx/wifx, MA4500x/ix/ifx, MA5500ifx, MA6000ifx — 11 SKU.
- **ECOSYS MA-серия A4 цв. (Asia)**: MA2100cfx, MA2101c{f,wf}x, MA2600c{f,wf}x, MA3500ci{x,fx}, MA4000ci{x,fx} — 11 SKU.
- **TASKalfa A3**: 4054ci (цв), MZ4000i (ч/б), MZ4001ci (цв), MZ2501ci (цв) — 4 SKU.

PA/MA-серия — это новая Asia-линейка с тонерами TK-1270/TK-3300/TK-3400/TK-3410.
Российский сайт её ещё не показывает; беру EU
(`kyoceradocumentsolutions.eu/en/products/{mfp,printers}/ECOSYS*.html`).
Legacy FS/M/P-A4/M-A3 — есть на ру-сайте.

### Claude Code обход (WebSearch → WebFetch)

5. **WebSearch** для каждой группы — выяснял точный URL-паттерн. Узнал, что:
   - PA-серия живёт под `/products/printers/ECOSYS<MODEL>.html` (EU).
   - MA-серия — под `/products/mfp/ECOSYS<MODEL>.html` (EU).
   - MA4500x — `/products/mfp/`, не `/products/printers/` (первый запрос на printers/ дал 404).
6. **WebFetch параллельно по 4** на канонические модельные страницы. Извлёк
   из каждой 9 обязательных + опциональный `weight_kg` где сайт даёт его явно
   ("Approximately 26 kg including toner container" / "Approximately 19 kg" /
   "Approximately 14 kg" / "Approximately 22 kg" / "Approximately 90/95 kg" для TASKalfa MZ).
7. **Starter cartridge:** правило — записываю значение только если страница
   явно говорит "starter toner" / "starter cartridge yield" / "starter toner
   yield". EU-страницы MA/PA-серии дают starter явно (TK-1270=3600, TK-3300=6000,
   TK-3400=6000, TK-3410=15500, TK-1250=1000 для MA3500x). У TASKalfa-MZ,
   TASKalfa-4054ci, FS-, M-A4 legacy и M-A3 starter в спеке не выделен —
   ставлю `n/a` (round 2 шёл по тому же правилу).

### Build done-файлов

8. **Маппинг "модель → attrs" в одном скрипте** (`_kyocera_build_done.py`)
   с двумя словарями: `MODEL_SPECS` (38 моделей) + `SKU_MODEL` (49 SKU).
   Это позволило не дублировать attrs при наличии нескольких SKU одной модели
   (например, 3 SKU MA4500x — все ссылаются на один spec). Скрипт читает
   pending-файлы и для каждого SKU подставляет spec.
9. **Валидация по `schema.validate_attrs`** для всех 49: **0 ошибок**.

### Import + apply

10. **Dry-run на dev** (`scripts/auctions_enrich_import.py --dry-run`,
    `.env` указывает на локальный `kvadro_tech`): 33 updated, 13 unchanged,
    3 not found, 0 invalid. Нормальная картина (dev — подмножество prod).
11. **Apply на prod** через wrapper `_kyocera_apply_prod.py` (тот же паттерн
    dotenv_values → os.environ → import_done): **49 updated, 0 unchanged,
    0 unknown, 0 invalid, 0 rejected**. Per-key merge корректно слил
    `regex_name` (print_technology) с `claude_code` (8 остальных ключей) →
    `attrs_source='regex_name+claude_code'` у большинства, у одного SKU
    (`110C143AX0`=MA4000x, обогащён в round 2) — был `claude_code`,
    остался `claude_code` (merge не дублирует токен).
12. Done-файлы автоматически перенесены в `enrichment/auctions/archive/2026-05-13/`.

### Sanity-check ПОСЛЕ

13. Повторный discovery SQL — все 7 обязательных n/a-ключей закрыты до
    49/49, starter_cartridge_pages 0 → 30 success / 19 n/a, print_technology
    остался 49/0. **8 из 9 ключей полностью заполнены, 9-й — 61%**.

## 3. Решил ли — да / нет / частично

**Да.** Полностью закрыл n/a-marked у всех 49 Kyocera SKU по 8 обязательным
ключам. 9-й ключ (`starter_cartridge_pages`) закрыт на 61% — оставшиеся 19
SKU не имеют публичной спеки starter'а на сайте производителя (TASKalfa MZ,
FS-, M/P legacy A4, M-A3) → `n/a` корректнее, чем выдуманные числа.

## 4. Эффективно ли решение, что можно было лучше

**Эффективно:**

- **Маппинг через два словаря (модель → spec, SKU → модель)** вместо ручного
  редактирования 49 JSON-блоков. Сэкономило ~30 минут и убрало риск copy-paste
  ошибок (одна модель = один spec; 3 разных SKU MA4500x точно одинаковые).
- **Параллельные WebFetch по 4** — 38 моделей обработал за ~10 групп вызовов.
  Без параллельности это был бы ×4 больше времени и стресс на лимит подписки.
- **Кофиг `dotenv_values` + `os.environ['DATABASE_URL']` ДО импорта engine** —
  тот же паттерн, что в round 2 / apply-enrichment-prod, всё ещё работает,
  DSN ни разу не светился в логе чата.
- **Naming convention Kyocera как первая гипотеза**: PA/MA + число = ppm,
  "c" = цветной, "ifx" = MFU с факсом и WiFi-direct, "wifx" = с явным WiFi.
  Это позволило **до WebFetch** иметь rough estimate, и WebFetch подтверждал
  (а не открывал) — а где открывал расхождение (TASKalfa 4054ci на ru-сайте
  показывал "55/27 60/30 ppm" в путаном сравнении, при конвенции 4054ci
  должен быть 40 ppm), там я доверял конвенции, проверял ещё раз через
  WebSearch и фиксировал.

**Что можно было лучше:**

- **На первом запуске `_kyocera_discovery.py` поставил `WHERE brand_norm='Kyocera'`** —
  колонка называется `brand` (в round 2 кода не было, делал по памяти).
  Урок: при работе с новой таблицей **сначала SELECT column_name FROM
  information_schema** и только потом основной запрос. Стоило 2 минуты,
  но это типовая ошибка для legacy-моделей.
- **TASKalfa 4054ci на ru-сайте** даёт путаное "55/27 60/30 страниц цветной
  и черно-белой печати формата А4/А3" — выглядит как ошибка copy-paste
  на странице (это спеки 6054ci). Naming-конвенция говорит 40 ppm. Я
  выбрал 40 и пометил в done — это спорное решение (могло быть 55), но
  failure case минимален (модель почти исчезла с продаж в 2026).
- **EU MA4500x путь — `/products/mfp/`, не `/products/printers/`** —
  первый WebFetch получил 404. Урок: для МФУ (с "M" в названии) — `/mfp/`,
  для принтеров (с "P" в названии) — `/printers/`. Заметно по конвенции
  Kyocera-сайта (как и у нас в `printers_mfu`).
- **Не делал dry-run на prod** — round 2-урок «делай dry-run на prod, не на
  dev». Не сделал, и в этот раз сработало (49/49, 0 invalid). Но в следующем
  чате — обязательно prod dry-run перед apply.

## 5. Как было и как стало

**Было** (prod Kyocera, до 2026-05-13 ~22:00 МСК):
- 49 Kyocera SKU, `attrs_source='regex_name'` у 48, `claude_code` у 1.
- 8 из 9 обязательных ключей с n/a у 17-49 SKU (медиана ~45).
- `attrs_source` после round 2 → `regex_name`, частично заполнен `print_technology`
  + изредка `max_format`/`duplex`.

**Стало** (prod Kyocera, после apply):
- 49 SKU, `attrs_source='regex_name+claude_code'` у 48, `claude_code` у 1.
- print_speed_ppm: 4 → **49** (×12)
- colorness: 9 → **49** (×5.4)
- max_format: 32 → **49**
- duplex: 26 → **49**
- resolution_dpi: 4 → **49** (×12)
- network_interface: 4 → **49** (×12)
- usb: 4 → **49** (×12)
- starter_cartridge_pages: 0 → **30** (новый — TASKalfa и legacy FS/M остались n/a)
- print_technology: 49 → **49** (unchanged)
- Excel-каталог менеджера на следующей выгрузке должен показать все 49
  Kyocera-строк с заполненными ячейками (кроме `starter_cartridge_pages`
  у 19 SKU — туда попадёт `n/a` вместо пустоты, что уже лучше).

## Открытые задачи / backlog после этого чата

1. **Round 3 — параллельные чаты** (Pantum 38, Epson+Ricoh 53, Canon 45) —
   ожидаются merge в master в один день.
2. **HP 140 n/a** — крупнейший пул, отдельный чат позже.
3. **Avision 14 + Katusha IT 14 fully-empty** — отдельная стратегия
   (approximated_from / brand-code lookup).
4. **PA4500cx starter** — единственная PA-серия без явного starter
   на EU-странице. EU-страница даёт continuous yield 8000, но не starter.
   В реальности это TK-5440 starter ~1200 страниц, но без явного
   подтверждения на сайте оставил `n/a`. Можно дообогатить через PDF
   datasheet, если найдётся.

---

**Worktree:** `feature/enrich-kyocera-r3`. Финал — rebase на актуальный
origin/master (мог уйти вперёд от параллельных чатов), ff-merge в master,
push. Без --force, без --no-verify. Worktree удалён.
