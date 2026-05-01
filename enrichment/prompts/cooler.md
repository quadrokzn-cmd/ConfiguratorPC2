# Обогащение характеристик СИСТЕМ ОХЛАЖДЕНИЯ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md`.

## Целевые поля
| Поле | Тип | Допустимый диапазон / значения |
|---|---|---|
| `max_tdp_watts` | int | 30..500 (макс. поддерживаемая теплоотдача процессора) |
| `supported_sockets` | list[str] | непустой массив сокетов. Intel: `LGA1150`, `LGA1151`, `LGA1155`, `LGA1156`, `LGA1200`, `LGA1700`, `LGA1851`, `LGA2011`, `LGA2011-3`, `LGA2066`, `LGA1366`. AMD: `AM3`, `AM3+`, `AM4`, `AM5`, `FM1`, `FM2`, `FM2+`, `sTRX4`, `sWRX8`, `TR4`, `SP3`, `SP5`. |

> `max_tdp_watts` — это **маркетинговая** величина, иногда производитель
> её просто не публикует. Берём ровно то, что указано на странице товара
> у вендора (поле «TDP support», «Heat Dissipation up to», «CPU TDP up to»).
>
> **Если на оф. сайте этого числа нет** — возвращай
> `{"value": null, "source_url": null, "reason": "not published"}`.
> **НЕ оценивай «на глаз»**: ни по бюджетному классу, ни по типу
> радиатора (башенный/двойной/AIO), ни по размеру вентилятора
> (120/140/240/360 мм), ни по аналогии с другой моделью того же
> вендора. Расчётный TDP из размера радиатора уже посчитан
> derived-правилами на нашей стороне — лишний guesstimate только
> создаёт ложноположительные значения.

## Где искать
**Whitelist оф. доменов кулеров** (только эти URL пройдут валидатор;
вне списка — `null` + reason):
`thermalright.com`, `arctic.de`, `arctic.ac`, `noctua.at`, `corsair.com`,
`deepcool.com`, `gamerstorm.com`, `bequiet.com`, `coolermaster.com`,
`alseye.com`, `idcooling.com`, `pccooler.com.cn`.

Магазины (DNS, Citilink, Ozon, Wildberries, Amazon, Newegg) и
агрегаторы/обзорщики (3DNews, Tom's Hardware, TechPowerUp, iXBT) —
**не источники**. См. также пункт 2 в `_общие_правила.md`.

- **Thermalright** — `thermalright.com/product/<series>/`. Раздел
  Specifications: «TDP Support», «Compatible Socket».
- **ARCTIC** — `arctic.de/en/<series>` → Tech Specs. «Heat Dissipation
  up to NN W», «CPU Socket Compatibility».
- **Noctua** — `noctua.at/en/<series>` → CPU TDP guidelines (для разных
  сокетов). Берём верхнюю границу для современных сокетов.
- **DeepCool / GamerStorm** — `deepcool.com/products/<series>/<model>`,
  `gamerstorm.com/product/...`.
- **Corsair / be quiet! / Cooler Master** — `corsair.com`, `bequiet.com`,
  `coolermaster.com`. Смотри «Specifications» или «Compatibility».
- **ID-Cooling** — `idcooling.com/.../products/...`.
- **PCCooler** — `pccooler.com.cn/products/...`.

## Нормализация сокетов
Возвращай канонические имена (uppercase, без пробелов):
- Intel: `LGA1150`, `LGA1151`, `LGA1155`, `LGA1156`, `LGA1200`, `LGA1700`,
  `LGA1851`, `LGA2011`, `LGA2011-3`, `LGA2066`, `LGA1366` (заметь: в
  наших прайсах часто пишется `1700` или `Soc-1700` — возвращай всё
  равно `LGA1700`).
- AMD: `AM5`, `AM4`, `AM3`, `AM3+`, `AM2`, `FM2`, `FM2+`, `FM1`.
- AMD HEDT/серверные: `sTRX4`, `sWRX8`, `TR4`, `sTR4`, `SP3`, `SP5`, `SP6`.
- **Если сайт указывает обобщённо `LGA 115x` / `LGA115X`** (компактное
  обозначение у бюджетных кулеров) — **разверни в массив отдельных
  сокетов**: `["LGA1150", "LGA1151", "LGA1155", "LGA1156"]`. НЕ возвращай
  `LGA115X` как одно значение.

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
