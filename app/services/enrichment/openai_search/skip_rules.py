# Правила «правильных NULL» для новых SKU.
#
# Идея: если для конкретного (категория, поле) у конкретного производителя
# значение принципиально не публикуется на оф. сайтах (или должно быть NULL
# по логике компонента), мы НЕ дёргаем OpenAI — сразу пишем в
# component_field_sources source='null_by_rule' и идём дальше. Это экономит
# стоимость и не создаёт фантомные «ошибки поиска».
#
# Правила — просто данные, легко дополняются. Проверка идёт через функцию
# should_skip(category, field_name, row). row — dict с полями компонента
# (minimum: manufacturer, model, sku и все поля из TARGET_FIELDS[category]).

from __future__ import annotations

from typing import Any, Callable


# Один элемент = описание правила.
# predicate принимает current_row (dict) и возвращает True, если поле
# нужно пропустить (NULL в этом случае — «правильный NULL»).
# reason — короткий код для отчёта.
class Rule:
    __slots__ = ("category", "field", "predicate", "reason")

    def __init__(
        self,
        category: str,
        field: str,
        predicate: Callable[[dict], bool],
        reason: str,
    ) -> None:
        self.category = category
        self.field = field
        self.predicate = predicate
        self.reason = reason


def _mfg_in(*names: str) -> Callable[[dict], bool]:
    names_norm = {n.lower() for n in names}
    def _check(row: dict) -> bool:
        v = (row.get("manufacturer") or "").strip().lower()
        return v in names_norm
    return _check


def _model_contains(*substrings: str) -> Callable[[dict], bool]:
    subs = [s.lower() for s in substrings]
    def _check(row: dict) -> bool:
        v = (row.get("model") or "").lower()
        return any(s in v for s in subs)
    return _check


def _field_is_false(field_name: str) -> Callable[[dict], bool]:
    """True, если в текущей строке field_name == False (строгое сравнение)."""
    def _check(row: dict) -> bool:
        return row.get(field_name) is False
    return _check


# Любая подходящая категория. Если поле не покрывается ни одним правилом —
# оно пойдёт в OpenAI.
RULES: list[Rule] = [
    # --- case -------------------------------------------------------------
    # Если в корпусе явно нет БП — ищущее значение included_psu_watts NULL
    # по определению. Это главный источник «правильных NULL».
    Rule(
        category="case", field="included_psu_watts",
        predicate=_field_is_false("has_psu_included"),
        reason="case_without_psu",
    ),

    # --- cooler -----------------------------------------------------------
    # Производители, которые TDP не публикуют (проверено на материале 2.5Б).
    Rule(
        category="cooler", field="max_tdp_watts",
        predicate=_mfg_in(
            "Thermalright", "ARCTIC", "Corsair", "HP", "Lenovo",
            "ACD Systems", "ALSEYE CORPORATION LIMITED",
        ),
        reason="mfg_does_not_publish_tdp",
    ),
    # supported_sockets в 2.5Б не нашёлся для OEM-кулеров.
    Rule(
        category="cooler", field="supported_sockets",
        predicate=_mfg_in(
            "HP", "Lenovo", "ACD Systems", "Raspberry Pi Foundation",
            "Chenbro",
        ),
        reason="oem_cooler_no_public_sockets",
    ),

    # --- gpu --------------------------------------------------------------
    # Производители без публичных spec-страниц для игровых характеристик.
    # AFOX — серверно/офисные карты; Matrox — профессиональные,
    # использует другие метрики (не tdp_watts/core_clock_mhz в нашем виде).
    *[
        Rule(
            category="gpu", field=f,
            predicate=_mfg_in("AFOX CORPORATION", "Matrox"),
            reason="mfg_no_public_gpu_specs",
        )
        for f in (
            "tdp_watts", "core_clock_mhz", "memory_clock_mhz",
            "needs_extra_power", "video_outputs",
        )
    ],

    # --- psu --------------------------------------------------------------
    # PoE-инжекторы, адаптеры питания, USB-зарядки и т.п. — не полноценные БП.
    Rule(
        category="psu", field="power_watts",
        predicate=_model_contains(
            " poe ", "poe-", "инжектор", "адаптер ", "адаптер пит",
            "ac-dc adapter", "power adapter", "power injector",
        ),
        reason="not_a_pc_psu",
    ),

    # --- storage ----------------------------------------------------------
    # Cisco — серверные диски с OEM-кодировкой, в прайсе несколько штук.
    Rule(
        category="storage", field="interface",
        predicate=_mfg_in("Cisco"),
        reason="oem_storage_interface_not_public",
    ),
]


def should_skip(category: str, field_name: str, row: dict) -> str | None:
    """Проверяет, нужно ли пропустить поле. Возвращает код причины или None.

    Если возвращён код — поле пишется в component_field_sources с
    source='null_by_rule' и OpenAI не дёргается.
    """
    for rule in RULES:
        if rule.category == category and rule.field == field_name:
            try:
                if rule.predicate(row):
                    return rule.reason
            except Exception:
                # плохое правило не должно ломать прогон
                continue
    return None
