# Повторный enrichment печатной техники на prod, round 2 (empty-пул, 2026-05-13)

## 1. Какая задача была поставлена

Собственник скачал Excel-каталог печатной техники после фикса orchestrator-mfu-category (`cf7265c`) и колонок наличия (`5b85e05`) и увидел, что у большинства Pantum/HP/Katusha IT/Ricoh строк колонки характеристик (`print_speed_ppm`, `colorness`, `max_format`, `duplex`, `resolution_dpi`, `network_interface`, `usb`, `starter_cartridge_pages`, `print_technology`) **буквально пустые — не `n/a`**. Только нижние строки с `attrs_source='regex_name'` показывали реальные значения или маркер `n/a`.

Брифинг оркестратора:

1. Discovery prod-БД по трём категориям (success / n/a_marked / empty) для каждого из 9 ключей `PRINTER_MFU_ATTRS`, плюс топ-бренды empty-пула и причина в коде.
2. Запустить regex_name pass (дешёво, закрывает часть автоматически).
3. Обогатить топ-бренды empty через Claude Code (WebSearch + WebFetch официальных сайтов).
4. Apply на prod через `auctions_enrich_import.py`.
5. Sanity-check Excel: подсчитать, насколько уменьшился empty-пул.
6. Обновить план + написать рефлексию; для остатка — готовый промт-шаблон.

## 2. Как я её решал

### Discovery prod-БД

1. **Подключение к prod без светивания DSN в чат:** `dotenv_values('.env.local.prod.v1')` (не `Grep output_mode=content`), достал `DATABASE_PUBLIC_URL`, выставил `os.environ['DATABASE_URL']` ДО импорта `shared.db.engine` (как в чате apply-enrichment-prod 2026-05-12).
2. **SQL по трём категориям:** для каждого из 9 обязательных ключей — `CASE WHEN attrs_jsonb IS NULL OR NOT (attrs_jsonb ? key) THEN 'empty' WHEN attrs_jsonb->>key = 'n/a' THEN 'n/a_marked' ELSE 'success' END`. Дополнительно: `attrs_source` распределение, `attrs_jsonb = '{}'::jsonb` count, top-бренды empty, top-бренды n/a-marked, hist `created_at` для понимания «когда родились пустые».
3. **Цифры ДО:** total=496, empty (`attrs_jsonb={}` или NULL) = **358 SKU (72%)**. По брендам: HP 138, Pantum 60, Katusha IT 38, Ricoh 33, Kyocera 26, Canon 17, Avision 14, Sindoh 7, Bulat 6, Xerox 6, Epson 4, Konica Minolta 4. Все 358 — `attrs_source IS NULL`, `attrs_updated_at IS NULL`.

### Discovery в коде (почему 358 пустых)

`portal/services/configurator/price_loaders/orchestrator.py::_create_printers_mfu_skeleton` (lines 175-228, doctring «Этап 9a-enrich (2026-05-10)») при INSERT нового SKU вызывает `parse_printer_attrs(name)`. Если parsed пуст — пишет `attrs_jsonb={}`, `attrs_source=NULL`. Иначе — все 9 ключей + `regex_name`-source.

Хронология `created_at` показала ключевой факт: **357 из 358 пустых SKU родились 2026-05-10** (первичный импорт каталога C-PC2 в этап 9 слияния, ДО интеграции 9a-enrich в orchestrator). 2026-05-11 (1), 2026-05-12 (91 c 1 empty), 2026-05-13 (8 c 0 empty) — новые SKU идут через orchestrator с регексом автоматически. То есть **разовый исторический разрыв**, не текущая утечка.

### Стратегия (принял сам, без AskUserQuestion)

- **Регекс — дешёвый первый проход.** `enrich_printers_mfu_from_names.py` ходит по ВСЕМ SKU и заполняет `n/a`-ключи (включая отсутствующие). На 2026-05-12 он уже стрелял (90 SKU обновил), но 268 SKU остались. Сейчас прогон закроет их.
- **Claude Code — только для самых жирных целей.** Из 358 empty два пула:
  - SKU, у которых регекс что-то извлёк → останутся с `regex_name`-source, частичный success. Чтобы их добить — Claude Code по топ-бренду (**Pantum**, core по CLAUDE.md, batch 001 — 25 SKU BM/BP/CM-серий).
  - SKU, где регекс ничего не извлёк → останутся fully empty. Их 51 после regex (Avision 14 + Katusha IT 14 — cryptic names; Ricoh 11, Sindoh 5, Xerox 4, Konica Minolta 2, Kyocera 1 — конкретные модели с известными спеками). **23 не-Avision/Katusha** беру в Claude Code.
- **HP 140 / Canon 45 / Kyocera 45 / Epson 30 — backlog** на следующий чат (готовый промт-шаблон ниже). Avision и Katusha IT cryptic-names — отдельная стратегия (approximated_from / brand-code lookup).

### Regex pass apply

`DATABASE_URL=<prod_DSN> python scripts/enrich_printers_mfu_from_names.py --apply` без правок скрипта. **307 SKU обновлены за один transaction**, 566 ключей суммарно. Топ по ключам: print_technology +251, colorness +141, max_format +61, network_interface +23, print_speed_ppm +37, resolution_dpi +22, usb +26, duplex +5, starter_cartridge_pages +0. По брендам: HP 138, Pantum 60, Kyocera 25, Katusha IT 24, Ricoh 22, Canon 17, Bulat 6, Epson 4, Sindoh 2, Konica Minolta 2, Xerox 2. Per-row UPDATE через single SQLAlchemy session+commit — на Railway-proxy уложился. Empty пул: 358 → **51** (Avision 14 + Katusha IT 14 + Ricoh 11 + Sindoh 5 + Xerox 4 + Konica 2 + Kyocera 1).

### Pending-файлы

`auctions_enrich_export.py` берёт только `attrs_jsonb IS NULL OR attrs_jsonb = '{}'::jsonb`. После regex pass Pantum SKU имеют `attrs_jsonb={...}` с `n/a` — exporter их не подберёт. Поэтому я собрал pending JSON напрямую SQL'ом из prod-БД (для Pantum 63 SKU + 23 fully-empty), записал в `enrichment/auctions/pending/*round2*.json` по бренду с batch_id 25 SKU/файл.

### Claude Code обход

- **Pantum batch 001 (25 SKU):** WebSearch для понимания URL-паттерна pantum.ru. Параллельные WebFetch (4 за раз) на `https://www.pantum.ru/products/laser-devices/<тип>-<модель>/`. Параллельность 4 уперлась в `feedback_subagent_parallelism` (потолок 4-5), 404 на цветных моделях из-за URL-paттерна `cvetnoe-mfu-cm1100dn-2/` вместо ожидаемого `tsvetnoe-`. Поправил через дополнительный WebSearch. Тип печати у всех Pantum в batch 001 = «лазерная», colorness корректно различается между BM/BP (ч/б) и CM (цветной). Speed: BM1800/BP1800 = 18 ppm, BM2300/BP2300 = 22, BM5100/BP5100 = 40, BM5201/BP5200 = 42, CM1100A = 18, CM2100A = 20.
- **Sindoh 5:** WebSearch + WebFetch sindoh.com (404 на A500dn, но manualslib + russian-резеллеры дали полные спеки). A500dn/M500 = 34 ppm 2400 dpi A4. C300 = 24 ppm 600 dpi A4 цветной. D330e/D332e = 22/28 ppm 1800 dpi A3 цветной. Starter cartridge — у части n/a.
- **Xerox 4:** Phaser 3020BI = 21 ppm ч/б A4 WiFi. VersaLink B415DN = 50 ppm ч/б A4 LAN. VersaLink C415DN = 42 ppm цветной A4 LAN. AltaLink C8230/35 IOT (`C8201V_F`) = 30 ppm цветной A3 LAN.
- **Konica Minolta 2:** Bizhub C450i = 45 ppm цветной A3. Bizhub C251i = 25 ppm цветной A3. Оба LAN.
- **Kyocera 1:** ECOSYS MA4000x = 40 ppm ч/б A4 LAN.
- **Ricoh 11:** SP 230 DNw/SFNw = 30 ppm A4 LAN+WiFi. MP 2014AD ×2 = 20 ppm A3 LAN. MP 305+ SPF = 30 ppm A3 LAN. M 2700/M 2701/IM 2702 = 27 ppm A3 LAN. IM 2500 = 25 ppm A3, IM 3500 = 35 ppm A3, IM 4000A = 40 ppm A3.

Все 48 SKU прошли `validate_attrs` (0 ошибок), уложены в 6 done-файлов.

### Import + apply

Сначала dry-run на dev-БД (engine из `.env`, локальный kvadro_tech) — 13 SKU updated, 33 unchanged, 2 not found. Это нормально: dev-БД содержит подмножество prod, importer не падает.

Затем prod apply через тот же python heredoc с `DATABASE_URL=<prod_DSN>`: **6 files imported, 48 SKU updated, 0 unknown, 0 invalid, 0 rejected**. Per-key merge корректно слил Pantum-regex_name + claude_code → `regex_name+claude_code`-source (25 SKU). Done-файлы перемещены в `enrichment/auctions/archive/2026-05-13/`.

### Sanity-check

Финальный SQL по 3 категориям прогнан повторно. Excel-выгрузка `python scripts/catalog_excel_export.py --target printers --output /tmp/sanity_round2` — открыл openpyxl'ем, прошёлся по spec-колонкам обоих листов.

## 3. Решил ли — да / нет / частично

**Да** по основной цели (пустота в Excel-каталоге исчезла), **частично** по полноте характеристик (часть SKU всё ещё с n/a).

Что **сделано**:
- Прод-БД больше не имеет 358 SKU с `attrs_jsonb={}` — осталось 28 (Avision 14 + Katusha IT 14, cryptic names).
- Заполнение success вырос везде: print_speed_ppm 39 → 124 (×3), starter_cartridge_pages 3 → 30 (×10), print_technology 137 → 411 (×3), resolution_dpi 39 → 109 (×2.8), duplex 89 → 141.
- Excel-каталог собственника: на листе «Принтеры» 134/136 строк имеют не-пустые ячейки, на «МФУ» 334/360. Только Avision (МФУ) и Katusha IT M-series (МФУ) остались пустыми — это 28 SKU всего.
- План `plans/2026-04-23-platforma-i-aukciony.md` обновлён мини-этапом со всеми цифрами.

Что **не сделано / открыто**:
- Pantum batch 2/3 (38 SKU n/a). Pending-файлы готовы, ждут следующий чат.
- HP 140 n/a (массив, нужен отдельный чат / параллельная стратегия).
- Canon 45 / Kyocera 45 / Epson 30 / Ricoh 23 n/a-marked — backlog.
- Avision 14 + Katusha IT 14 fully-empty — требуют approximated_from / brand-code lookup.

## 4. Эффективно ли решение, что можно было лучше

**Эффективно:**

- **Стартовое discovery — 3-стрелочное (success / n/a_marked / empty + хронология `created_at`).** Сразу видно: 357 пустых из 358 родились в один день (2026-05-10, этап 9 импорт). Это снимает гипотезу «утечка из price_loaders» и фиксирует «исторический разрыв». Урок из прошлого чата (apply-enrichment-prod) был учтён.
- **Регекс pass — 307 SKU за один проход.** Закрыл 566 ключей. После него осталось 51 SKU для Claude Code вместо изначальных 358. Это правильный порядок: дешёвое первым, дорогое после.
- **Параллельные WebFetch (4 за раз).** Pantum batch 001 (25 SKU) уложился за ~6 групп параллельных вызовов. Без параллельности это был бы ×4 больше времени.
- **Pending JSON напрямую SQL'ом.** `auctions_enrich_export.py` фильтрует только `attrs_jsonb IS NULL OR ='{}'`, после regex pass он бесполезен для n/a-marked SKU. Собрал pending руками — экономия часа на ручном «нужно ещё один CLI».
- **`os.environ['DATABASE_URL'] = prod[...]`** перед импортом engine — повтор паттерна из 2026-05-12 apply, всё ещё работает.

**Что можно было лучше:**

- **На URL-паттернах Pantum я сначала угадал `tsvetnoe-`, оказалось `cvetnoe-`.** 2 × 404 за один параллельный батч → пришлось делать дополнительный WebSearch. Урок: для незнакомого сайта **первым делом** делать WebSearch (а не угадывать URL-pattern), даже если предыдущий батч URL'ов сработал.
- **Pantum batch 001 25 SKU занял основную долю чата.** Был соблазн пойти ещё в batch 002/003. Решил остановиться на 25 + 23 fully-empty = 48 SKU и оставить чёткий backlog. По объёму прошлого чата (60 SKU) — это сопоставимо, но в этот раз я был уверен, что прод-apply пройдёт чисто (48/48 updated, 0 unknown — против 39/60 в прошлый раз).
- **Starter_cartridge_pages — самое слабое место.** Многие производители (Ricoh, Xerox, Konica Minolta) не публикуют стартовый картридж в общедоступном виде на model-странице — нужно копать в брошюры / спецификации в PDF. Я ставил `n/a` для большинства не-Pantum. Это снимает фокус матчинга по этому атрибуту (он fail-open), но Excel-каталог менеджера остаётся с пустой ячейкой. Backlog: попробовать `WebFetch` на каждый PDF brochure, если он найден в WebSearch.
- **Dry-run на dev-БД ничего полезного не дал** (другой набор SKU, 2 unknown). Раньше делал dry-run **на prod**, чтобы сразу видеть фактическую дельту. В этот раз тоже надо было — было бы +2 минуты, но рискну sicherer.
- **Я не запустил `scripts/run_matching.py` после apply** в этом чате. В прошлом чате matching раскопал большую дельту (0 → 268 matches). В этот раз my apply не должен был сильно изменить matching (большинство затронутых ключей — print_technology/colorness/max_format/duplex, не критичные KTRU-match-атрибуты), но это не проверено. Backlog: посмотреть `matches` через сутки.

## 5. Как было и как стало

**Было** (prod, до 2026-05-13 17:50 UTC):
- 496 SKU printers_mfu, **358 пустых** (`attrs_jsonb={}`, `attrs_source=NULL`), 99 с regex_name, 39 с claude_code.
- print_speed_ppm: 39 success / 99 n/a / 358 empty.
- print_technology: 137 success / 1 n/a / 358 empty.
- starter_cartridge_pages: 3 success / 135 n/a / 358 empty.
- Excel: ~358 строк × 9 spec-колонок = ~3200 пустых ячеек у собственника.

**Стало** (prod, после 2026-05-13 18:35 UTC):
- 496 SKU, **28 пустых** (Avision 14 + Katusha IT 14), 381 с regex_name, 62 с claude_code, 25 с regex_name+claude_code.
- print_speed_ppm: **124** success / 344 n/a / 28 empty (success ×3, empty ↓92%).
- print_technology: **411** success / 57 n/a / 28 empty.
- starter_cartridge_pages: **30** success / 438 n/a / 28 empty (success ×10).
- Excel: ~28 строк × 9 = ~252 пустых ячейки. Сокращение **−92%**.

**Что оставлено на следующий чат / собственника:**
1. Pantum batch 2/3 (38 SKU) — pending готовы.
2. HP 140 n/a по speed (крупнейший пул).
3. Canon 45 + Kyocera 45 + Epson 30 + Ricoh 23 n/a-marked.
4. Avision 14 + Katusha IT 14 fully-empty (cryptic names).
5. **Backlog-пункт расширения regex_name parser** для Pantum-паттерна (закрыл бы 60 n/a без Claude Code).
6. **Системное наблюдение:** при будущих миграциях каталога (этапы слияния / репо-консолидации) ОБЯЗАТЕЛЬНО прогонять `enrich_printers_mfu_from_names.py --apply` сразу после.

---

## Action items: промт-шаблон для следующего enrichment-чата

```
# Enrichment остатка печатной техники на prod (round 3)

Ты — исполнитель в репо QuadroTech-Suite (d:\ProjectsClaudeCode\ConfiguratorPC2).
Сегодня: 2026-05-14.
Работай в worktree:
  git fetch origin
  git worktree add -b feature/printers-enrichment-round3-2026-05-14 ../ConfiguratorPC2-enrichment-3 origin/master
  cd ../ConfiguratorPC2-enrichment-3

## Контекст

В чате 2026-05-13-printers-enrichment-round2 закрыт empty-пул prod (358 → 28).
Остаются n/a-marked по print_speed_ppm в брендах Pantum 38 / HP 140 / Canon 45 /
Kyocera 45 / Epson 30 / Ricoh 23. Pending-файлы Pantum 2/3 уже лежат в
`enrichment/auctions/pending/pantum_round2_{002,003}.json`.

## Задача

Приоритет 1: Pantum batch 002 + 003 (38 SKU) через pantum.ru.
   URL-паттерн: `https://www.pantum.ru/products/laser-devices/<тип>-<модель>/`.
   Тип: `monohromnoe-mfu-` / `monohromnyj-printer-` / `cvetnoe-mfu-` /
   `cvetnoj-printer-`. Если 404 — WebSearch site:pantum.ru и взять URL.

Приоритет 2: HP 140 n/a-speed. Разбить на 3-5 чанков по моделям-сериям
   (LaserJet Pro, OfficeJet, DesignJet и т.д.). Источники: hp.com, hp.varstreet.com.
   Большинство HP уже имеют partial regex_name attrs — Claude Code добивает
   speed/resolution/starter_pages.

Приоритет 3: Canon 45 + Kyocera 45 + Epson 30 + Ricoh 23 — после.

Avision 14 + Katusha IT 14 — НЕ В ЭТОМ ЧАТЕ (требуют отдельной стратегии
approximated_from / brand-code lookup).

## Технические правила
- prod-DSN: `dotenv_values('.env.local.prod.v1')['DATABASE_PUBLIC_URL']`,
  выставить `os.environ['DATABASE_URL']` ДО `from shared.db import engine`.
- Pending-файлы для HP/Canon/Kyocera/Epson/Ricoh n/a-marked — собирать
  напрямую SQL'ом из prod-БД (exporter не возьмёт attrs_jsonb != {}).
- WebFetch параллельно по 4 — потолок subagent (memory feedback_subagent_parallelism).
- `validate_attrs` перед commit done-файла, importer на prod.
- 0 emoji в коде/коммитах/документах.
- DoD: discovery до/после, цифры в рефлексии, план обновлён,
  rebase + ff-merge + push, worktree удалить.

## Параллельная стратегия (опционально — собственник решает)

Если хочется быстро: 4 worktree параллельно (HP / Canon / Kyocera / Epson),
каждый по своему бренду. Pantum 38 — отдельным 5-м чатом.
```

---

**Worktree:** `feature/printers-enrichment-2026-05-13`. Влит в master через rebase + ff-only merge. После пуша worktree удалён.
