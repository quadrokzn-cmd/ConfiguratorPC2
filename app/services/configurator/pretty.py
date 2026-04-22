# Форматированный вывод результата подбора в консоль.
#
# Используется только в CLI-скрипте scripts/build_config.py. Никакой логики
# подбора здесь нет — только презентация уже готового BuildResult.

from __future__ import annotations

from app.services.configurator.schema import BuildResult, Variant


_CATEGORY_LABELS = {
    "cpu":         "Процессор",
    "motherboard": "Мат. плата",
    "ram":         "ОЗУ",
    "gpu":         "Видеокарта",
    "storage":     "Накопитель",
    "psu":         "Блок питания",
    "case":        "Корпус",
    "cooler":      "Кулер",
}

_PATH_LABELS = {
    "default": "GPU требуется",
    "A":       "путь A — экономия на GPU",
    "B":       "путь B — экономия на CPU",
}


def _fmt_price(usd: float, rub: float) -> str:
    return f"${usd:>9,.2f} / {rub:>11,.2f} руб".replace(",", " ")


def _truncate(s: str, width: int) -> str:
    s = (s or "").strip()
    if len(s) <= width:
        return s
    return s[: width - 1] + "…"


def _format_variant(v: Variant) -> str:
    path = _PATH_LABELS.get(v.path_used, v.path_used)
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 98)
    header = f"Вариант: {v.manufacturer}  ({path})"
    if v.used_transit:
        header += "  [включает транзит]"
    lines.append(header)
    lines.append("=" * 98)

    for ch in v.components:
        label = _CATEGORY_LABELS.get(ch.category, ch.category)
        qty_suffix = f" ×{ch.quantity}" if ch.quantity > 1 else ""
        model = _truncate(ch.model, 52)
        price_total_usd = ch.chosen.price_usd * ch.quantity
        price_total_rub = ch.chosen.price_rub * ch.quantity
        stock_label = "транзит" if ch.chosen.in_transit else "stock"
        lines.append(
            f"  {label:<12}{qty_suffix:<4}  {model:<52}  "
            f"{_fmt_price(price_total_usd, price_total_rub)}  "
            f"{ch.chosen.supplier}  {stock_label}={ch.chosen.stock}"
        )
        if ch.also_available_at:
            alt = ch.also_available_at[0]
            lines.append(
                f"                    также у {alt.supplier}: "
                f"${alt.price_usd:.2f}, {'транзит' if alt.in_transit else 'stock'}={alt.stock}"
                + (f" (+ещё {len(ch.also_available_at) - 1})"
                   if len(ch.also_available_at) > 1 else "")
            )

    lines.append("-" * 98)
    lines.append(
        f"  {'ИТОГО':<16}{'':<54}  {_fmt_price(v.total_usd, v.total_rub)}"
    )

    if v.warnings:
        lines.append("")
        lines.append("Предупреждения:")
        for w in v.warnings:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def format_result(result: BuildResult) -> str:
    """Возвращает многострочное форматированное представление результата."""
    lines: list[str] = []
    lines.append(f"Статус: {result.status}")
    lines.append(
        f"Курс USD/RUB: {result.usd_rub_rate:.4f} (источник: {result.fx_source})"
    )

    if not result.variants:
        lines.append("")
        lines.append("Варианты не найдены.")

    for v in result.variants:
        lines.append(_format_variant(v))

    if result.refusal_reason:
        lines.append("")
        lines.append("Причины отказа:")
        for key, text in result.refusal_reason.items():
            lines.append(f"  [{key}] {text}")

    return "\n".join(lines) + "\n"
