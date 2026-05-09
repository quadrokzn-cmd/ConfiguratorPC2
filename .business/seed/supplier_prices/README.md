# Прайсы дистрибьюторов — seed для Волны 1А

Сюда собственник складывает Excel-прайсы 8 дистрибьюторов:

| Дистрибьютор | Имя файла (рекомендуется) | Адаптер |
|---|---|---|
| Merlion | `merlion_YYYY-MM-DD.xlsx` | готов в ConfiguratorPC2 |
| OCS | `ocs_YYYY-MM-DD.xlsx` | готов в ConfiguratorPC2 |
| Treolan | `treolan_YYYY-MM-DD.xlsx` | готов в ConfiguratorPC2 |
| Ресурс-Медиа | `resursmedia_YYYY-MM-DD.xlsx` | пишется в Волне 1А-α |
| ASBIS | `asbis_YYYY-MM-DD.xlsx` | пишется в Волне 1А-α |
| SanDisk | `sandisk_YYYY-MM-DD.xlsx` | пишется в Волне 1А-α |
| Марвел | `marvel_YYYY-MM-DD.xlsx` | пишется в Волне 1А-α |
| А1Тис | `a1tis_YYYY-MM-DD.xlsx` | пишется в Волне 1А-α |

## Как агент использует

- Стартует с любого набора файлов (минимум 2–3, можно дополнять итеративно).
- По имени файла определяет дистрибьютора (метод `detect()` в `BasePriceLoader`).
- Парсит Excel, складывает в `supplier_prices` + лог в `price_uploads`.
- Несмапплённые строки — в `unmapped_supplier_items` (если агент решит создать таблицу).

## Что важно

- Прайсы НЕ под NDA (подтверждено собственником 2026-04-24, см. pre-work #3 в плане).
- Эта папка добавлена в `.gitignore` — содержимое не уходит в репозиторий, только этот README.
