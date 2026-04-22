# Обогащение характеристик СИСТЕМ ОХЛАЖДЕНИЯ через Claude Code

Сначала прочти `_общие_правила.md`.

## Целевое поле
| Поле | Тип | Диапазон |
|---|---|---|
| `max_tdp_watts` | int | 30..500 (макс. поддерживаемая теплоотдача процессора) |

## Где искать
- **Thermalright** — `thermalright.com/product/<series>/`. Раздел
  Specifications, строка "TDP" / "TDP support" / "Cooling capacity".
- **ARCTIC** — `arctic.de/en/<series>` → Tech Specs. Часто указано как
  "Heat Dissipation up to 200W".
- **Noctua** — `noctua.at/en/<series>` → CPU TDP guidelines (диапазон
  для разных сокетов; берём верхнюю границу для современных сокетов).
- **DeepCool** — `deepcool.com/products/<series>/<model>`.
- **Corsair / be quiet! / Cooler Master** — аналогично, спецстраницы продукта.

## Важные нюансы
- **Корпусные вентиляторы** (например, ARCTIC P12, Thermalright TL-C12,
  пакеты Pure 120) — это НЕ кулеры процессора. У них нет понятия
  `max_tdp_watts`. Возвращай `null` с
  `reason: "Корпусной вентилятор, не процессорный кулер"`.
- **Лоу-профильные кулеры** (Thermalright AXP-90, Noctua NH-L9) — у них
  TDP support обычно 65–95 Вт. Берём именно «for» / «recommended TDP», а не
  пиковую возможность.
- **Лен/HP/Chenbro и др. OEM-кулеры** часто без публичных спецификаций —
  `null` с пояснением.

## Куда сохранить
`enrichment/done/cooler/batch_NNN.json`.
