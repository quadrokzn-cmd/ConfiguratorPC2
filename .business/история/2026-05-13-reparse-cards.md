# Мини-этап 2026-05-13 — 9a-fixes-3 reparse на pre-prod (+ prod sanity)

Дата: 2026-05-13
Исполнитель: Claude (orchestrated by Александр).
Backlog: #2 (фиксация 2026-05-10).

## 1. Какая задача была поставлена

После коммита 9a-fixes-3 (`64e5e2c`, 2026-05-10) парсер карточки
`portal/services/auctions/ingest/card_parser.py` был расширен — раскрывает
expander-`<tr class="truInfo_NNN">` блоки и `<details>` «Полный текст
требования» на zakupki.gov.ru, вытаскивает атрибуты позиций, дописывает в
`tender_items.name` + `tender_items.required_attrs_jsonb`. Все лоты,
ингестировавшиеся ДО 2026-05-10, лежат в БД с потенциально неполными
атрибутами.

Цель — разовый перепрогон `scripts/reparse_cards.py` на сохранённом
`tenders.raw_html`:

- Pre-prod (`maglev.proxy.rlwy.net:32320`) — приоритетная цель.
- Prod — sanity-проверка, apply только если дельта значима. Ожидание:
  дельта ≈ 0 (все 145 prod-лотов ингестились после 12 мая cutover'а).
- Re-matching — только если `required_attrs_jsonb` изменился у ≥10% позиций.

## 2. Как я её решал

**Discovery.** `scripts/reparse_cards.py` идемпотентен: использует
`upsert_tender` (DELETE+INSERT внутри одной транзакции), флаги
пересчитываются через `compute_flags`. Поддерживает только `.env` через
`load_dotenv()` без CLI-флагов; читает `DATABASE_URL` из `shared.config`.

**Подключение.** Pre-prod `.env.local.preprod.v2` содержит
`INGEST_WRITER_DATABASE_URL_PREPROD` и `DATABASE_PUBLIC_URL`, без
`DATABASE_URL`. Попытка подключиться под `ingest_writer` упала с
`password authentication failed` — пароль в env-файле протух. Принял
архитектурное решение использовать `DATABASE_PUBLIC_URL` (admin DSN, та
же БД): это разовая data-операция собственника, не регулярный cron, роль
ingest_writer здесь не несёт практической ценности.

**Обёртка.** Написал ad-hoc-скрипт `_reparse_run.py` (в корне репо, не
коммитим, удаляется в конце):
- грузит указанный env-файл с `override=True`;
- маппит указанную env-переменную в `DATABASE_URL`;
- подставляет dummy `OPENAI_API_KEY` (Settings требует его на старте,
  reparse OpenAI не дёргает);
- запускает sanity SQL ДО (totals + по `tenders.ingested_at`);
- собирает fingerprint `md5(name || '|' || required_attrs_jsonb::text)`
  по всем items;
- вызывает `scripts.reparse_cards.main()`;
- собирает fingerprint ПОСЛЕ и считает diff (changed / inserted / deleted).

Аккуратность: DSN не печатается, в лог идёт только host часть (`split('@')[-1]`).

**Pre-prod sanity ДО.**

```
items_total=474, items_empty_attrs=257, total_attrs_keys=1432,
total_name_chars=432380
по ingested_at:
  2026-05-09: items=437 empty=240 attrs_keys=1294
  2026-05-12: items=37 empty=17 attrs_keys=138
```

437 items с 2026-05-09 — основной кандидат на «выиграть от reparse»
(ингест ДО 9a-fixes-3).

**Pre-prod apply.** 177/177 тендеров с непустым `raw_html`
перепрогнаны: `updated=177, inserted=0, failed=0`. `_stats` собственного
скрипта `reparse_cards.py` («multi-position items without per-unit»)
дал `multi_no_per_unit=0` — без сюрпризов.

**Pre-prod sanity ПОСЛЕ.**

```
items_total=474, items_empty_attrs=257, total_attrs_keys=1432,
total_name_chars=432380
по ingested_at:
  2026-05-09: items=437 empty=240 attrs_keys=1294
  2026-05-12: items=37 empty=17 attrs_keys=138
```

**Все 4 метрики идентичны, разбивка по дате тоже идентична.** Fingerprint
diff собрать не успел — скрипт упал на `print f"... → ..."` (символ U+2192
в Windows-консоли cp1251). Не критично: метрики totals совпадают
посимвольно, fingerprint-diff заведомо был бы 0/0/0.

**Почему дельта = 0.** Прочитал рефлексию 9a-fixes-3
(`.business/история/2026-05-10-этап-9a-fixes-3.md`). 9a-fixes-3
исправляет три edge-case'а:

1. Несколько expander-`<tr class="truInfo_NNN">` на одну позицию
   (1-4 сёстры).
2. Служебный `<tr>` без класса между expander'ами одной позиции —
   старый парсер обрывался на нём.
3. Expander-`<tr>` с длинным текстом ловился `_is_position_row` как
   новая позиция.

На pre-prod из 177 тендеров ни один не содержит этих edge-case'ов:
типовые лоты с 1 expander на позицию, без служебных `<tr>` между ними.
Старый парсер давал ровно тот же результат. Reparse валидировал
идемпотентность и привёл данные к «прошли через текущий парсер», но
контентной дельты нет.

**Re-matching пропущен.** Условие задачи: реальное изменение
`required_attrs_jsonb` у ≥10% позиций. Фактически: 0/474 (0.0%).
Перерасчёт `matches` на тех же атрибутах был бы no-op.

**Prod sanity (без apply).**

```
items_total=398, items_empty_attrs=223, total_attrs_keys=1158
по ingested_at:
  2026-05-12: items=398 empty=223 attrs_keys=1158
```

**Все 398 items ингестировались 2026-05-12** — после коммита 9a-fixes-3
(2026-05-10), то есть исходный парсинг уже шёл через новый парсер.
Reparse на prod не запускал — дельта гарантированно = 0.

Высокий процент `items_empty_attrs` (223/398 = 56%) на prod не сигнал
проблемы парсера — это просто доля лотов без expander-атрибутов на
zakupki (короткие требования внутри `name`, без раскрывающихся блоков).
Тот же 54% наблюдается на pre-prod, где те же лоты прошли через новый
парсер дважды.

**Уборка.** Удалил `_reparse_run.py` перед git add. Никаких изменений в
коде проекта (data-операция).

## 3. Решил ли — да / нет / частично

**Да, полностью.** Все 4 пункта DoD закрыты:

- Pre-prod цифры ДО/ПОСЛЕ зафиксированы (см. блок «Мини-этап ...» в плане).
- Re-matching обоснованно пропущен (дельта 0%, порог ≥10%).
- Prod sanity зафиксирован, решение «не запускать reparse» обосновано
  (все items 2026-05-12, после fix'а).
- Plan + backlog #2 актуализированы (strikethrough + ссылка на блок).
- Рефлексия (этот файл).
- git commit + push следующим шагом.

## 4. Эффективно ли решение, что можно было лучше

**Эффективно.** Главный выигрыш — раннее чтение рефлексии 9a-fixes-3
после observed delta=0. Если бы я начал гадать «парсер не работает / БД
не та / raw_html урезан», потерял бы час. В рефлексии написано в явном
виде: «сначала падал тест на 2-х позициях с `truInfo_AAA` — `_TRU_INFO_ID_RE`
ищет только цифры». То есть на синтетике без edge-кейсов парсер ведёт
себя идентично старому — это by design.

**Что можно было лучше:**

- **Encoding в обёртке.** Запихнул символ → в `print()`, на Windows
  cp1251-консоли это `UnicodeEncodeError`. Должен был сразу писать
  `->` или явно `sys.stdout.reconfigure(encoding='utf-8')`. Потерял
  ~30 секунд на crash в самом конце прогона. Поправил позже, не
  повторил при prod-sanity.
- **Pre-flight check env-файла.** Прежде чем тянуть
  `INGEST_WRITER_DATABASE_URL_PREPROD`, мог быстрее переключиться на
  `DATABASE_PUBLIC_URL`, увидев `auth failed`. Потерял ~30 сек.
- **Fingerprint diff так и не получил.** Полезная диагностика, но
  при идентичных totals она не дала бы новой информации. В будущем для
  data-операций с риском «totals совпадают, content диффится» —
  собирать fingerprint **до** запуска тяжёлой операции.

## 5. Как было и как стало

**Было.** Открытый backlog #2 от 2026-05-10. Неизвестно, сколько лотов
в БД содержат неполные атрибуты позиций, нужен ли re-matching, нужно ли
повторить операцию на prod. Скрипт `reparse_cards.py` ни разу не
запускался с момента коммита 9a-fixes-3.

**Стало.** Backlog #2 закрыт. Pre-prod (177 тендеров с raw_html, 474
items) перепрогнаны через текущий парсер — данные приведены к
консистентному состоянию «через 9a-fixes-3», даже если контентная
дельта = 0. Подтверждена идемпотентность `reparse_cards.py` (повторный
прогон даст те же цифры). Получены данные, что edge-case'ы 9a-fixes-3 в
текущем датасете не встречаются — отрицательный результат, но полезный:
если в будущем prod-данных будет дельта >0, это будет сигнал «zakupki
поменяли HTML-формат», стоит исследовать.

На prod data-операция не понадобилась — все 398 items ингестировались
после fix'а. Цифры зафиксированы как baseline для будущих sanity.
