# Обогащение характеристик СИСТЕМ ОХЛАЖДЕНИЯ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md`.

## Целевые поля
| Поле | Тип | Допустимый диапазон / значения |
|---|---|---|
| `max_tdp_watts` | int | 30..500 (макс. поддерживаемая теплоотдача процессора) |
| `supported_sockets` | list[str] | непустой массив сокетов: `AM5`, `AM4`, `AM3`, `LGA1700`, `LGA1851`, `LGA1200`, `LGA1151`, `LGA1150`, `LGA2066`, `LGA2011`, `LGA1366`, `sTRX4`, `SP3`, `SP5`, ... |

> `max_tdp_watts` часто маркетинговая величина — берём именно ту, что
> заявлена производителем кулера на странице товара (а не у процессора
> или сокета). Расчётный TDP из размера радиатора уже посчитан
> derived-правилами на нашей стороне.

## Где искать
- **Thermalright** — `thermalright.com/product/<series>/`. Раздел
  Specifications: «TDP Support», «Compatible Socket».
- **ARCTIC** — `arctic.de/en/<series>` → Tech Specs. «Heat Dissipation
  up to NN W», «CPU Socket Compatibility».
- **Noctua** — `noctua.at/en/<series>` → CPU TDP guidelines (для разных
  сокетов). Берём верхнюю границу для современных сокетов.
- **DeepCool / GamerStorm** — `deepcool.com/products/<series>/<model>`,
  `gamerstorm.com/product/...`.
- **Corsair / be quiet! / Cooler Master** — спецстраницы продукта,
  смотри «Specifications» или «Compatibility».
- **ID-Cooling** — `idcooling.com/.../products/...`.
- **PCCooler** — `pccooler.com.cn/products/...`.

## Нормализация сокетов
Возвращай канонические имена (uppercase, без пробелов):
- `LGA1700`, `LGA1851`, `LGA1200`, `LGA1151`, `LGA1150`, `LGA2066`, `LGA2011`,
  `LGA1366` (заметь: в наших прайсах часто пишется `1700` или `Soc-1700` —
  возвращай всё равно `LGA1700`).
- `AM5`, `AM4`, `AM3`, `AM3+`, `AM2`, `FM2`, `FM2+`, `FM1`.
- `sTRX4`, `sTR4`, `sTRX5`, `SP3`, `SP5`, `SP6` — серверные/HEDT AMD.
- `LGA1150/1151/1155/1156` иногда обозначается обобщённо как `LGA115X`
  у бюджетных кулеров — если на сайте именно так, верни `LGA115X`.

Список не должен содержать дубликатов.

## Важные нюансы
- **Корпусные вентиляторы** (например, ARCTIC P12, Thermalright TL-C12,
  пакеты Pure 120, Hyper 200 без кулера, RGB-комплекты на корпус) —
  это НЕ кулеры процессора. У них нет ни `max_tdp_watts`, ни
  `supported_sockets`. Возвращай оба поля как
  `{"value": null, "source_url": null, "reason": "Корпусной вентилятор, не процессорный кулер"}`.
- **Лоу-профильные кулеры** (Thermalright AXP-90, Noctua NH-L9) — у них
  TDP support обычно 65–95 Вт. Берём именно «for» / «recommended TDP»,
  а не пиковую возможность.
- **Лен/HP/Chenbro и др. OEM-кулеры** часто без публичных спецификаций —
  `null` с пояснением.

## Куда сохранить
`enrichment/done/cooler/<имя_входного_файла>`.
