# Обогащение характеристик КОРПУСОВ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md`.

Обогащение Case — два прогона.
В первом прогоне целевые поля — `has_psu_included` и
`supported_form_factors`. После их импорта запускается второй прогон
только для `included_psu_watts` (см. `case_psu_pass: true` в заголовке
batch'а).

## 1-й прогон. Целевые поля
| Поле | Тип | Значения |
|---|---|---|
| `has_psu_included` | bool | `true`, если в комплекте идёт БП |
| `supported_form_factors` | list[str] | подмножество `{"E-ATX","ATX","mATX","ITX","XL-ATX","SSI-EEB","SSI-CEB"}` |

### Где искать
- **JONSBO** — `jonsbo.com/en/products/<series>` → Specification.
- **Fractal Design** — `fractal-design.com/products/cases/<series>/<model>` →
  Specifications → "Motherboard compatibility" + "Power supply".
- **Lian Li** — `lian-li.com/product/<series>/<model>`.
- **NZXT** — `nzxt.com/product/<model>` → Tech Specs.
- **Phanteks** — `phanteks.com/<series>` → Specifications.
- **Thermaltake** — `thermaltake.com/products/<series>` → Specifications.
- **AeroCool** — `aerocool.io/product/<series>/<model>`.
- **Montech** — `montechpc.com/product/<series>`.
- **Azza** — `azza.com.tw/product/<series>/<model>`.
- **Ocypus** — `ocypus.com/product/<series>` (часто корпус + БП в одной
  spec).
- **InWin** — `in-win.com/en/case/<series>` (модели IW-RS436 и др.).
- **Chenbro** — `chenbro.com/.../products/...` (серверные/индустриальные).
- **AICIPC** — `aicipc.com/.../products/...`.
- **Российские ОЕМ** — `formula-pc.ru`, `accord-pc.ru`, `kingprice.ru`,
  `acd-group.com`, `fox-line.ru`.
- **Raspberry Pi** (корпуса для Pi) — `raspberrypi.com/products/<case>`.
  Для таких корпусов `supported_form_factors` оставляй
  `{"value": null, "reason": "Корпус для одноплатного компьютера, не для ATX-плат"}`.

### Нормализация форм-факторов
Возвращай каноничные значения:
- "Mini-ITX" → `"ITX"`
- "Micro-ATX" / "M-ATX" → `"mATX"`
- "Extended ATX" / "EATX" → `"E-ATX"`
- "ATX" → `"ATX"` без изменений.

Иногда производитель пишет одной строкой: «Mid Tower ATX/mATX/ITX».
Это означает массив `["ATX", "mATX", "ITX"]` (без дубликатов).

### `has_psu_included`
- `true` — если в spec-листе явно указан встроенный БП мощностью N Вт.
- `false` — если "Power Supply: Not Included" / "Поддерживаемый БП:
  ATX, не входит в комплект" / нет упоминания мощности БП.

## 2-й прогон. Целевое поле
| Поле | Тип | Диапазон |
|---|---|---|
| `included_psu_watts` | int | 100..2000 |

Поле обогащается **только** для корпусов, где в БД уже зафиксировано
`has_psu_included = TRUE`. Берём номинальную мощность встроенного БП в
Вт (например, для корпуса Inwin EM039 со встроенным БП 450 Вт — `450`).

> На стороне БД корпуса с `has_psu_included = FALSE` уже помечены
> `not_applicable_no_psu` для `included_psu_watts` и в batch не попадут.
> Если такая позиция вдруг встретилась — это ошибка, верни `null` с
> пояснением.

## Куда сохранить
`enrichment/done/case/<имя_входного_файла>`.
