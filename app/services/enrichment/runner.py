# Основной прогон обогащения по категории.
#
# Для каждой категории:
#   - выбираем из БД позиции, у которых хотя бы одно обязательное поле NULL;
#   - прогоняем наименование через соответствующий regex-экстрактор;
#   - через persistence.apply_enrichment пишем в БД (если не dry-run);
#   - собираем статистику покрытия.

import logging

from sqlalchemy import text

from app.database import SessionLocal
from app.services.enrichment.base import CATEGORY_TO_TABLE
from app.services.enrichment.persistence import apply_enrichment
from app.services.enrichment.regex_sources import cooler as cooler_src
from app.services.enrichment.regex_sources import cpu as cpu_src
from app.services.enrichment.regex_sources import gpu as gpu_src
from app.services.enrichment.regex_sources import psu as psu_src
from app.services.enrichment.regex_sources import ram as ram_src
from app.services.enrichment.regex_sources import storage as storage_src

logger = logging.getLogger(__name__)

# Обязательные поля для каждой категории (из migrations/001_init.sql).
# Экстракторы регулярок отвечают только за эти поля; опциональные оставляем NULL.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "cpu": [
        "socket", "cores", "threads",
        "base_clock_ghz", "turbo_clock_ghz",
        "tdp_watts", "has_integrated_graphics",
        "memory_type", "package_type",
    ],
    "psu": [
        "power_watts",
    ],
    "ram": [
        "memory_type", "form_factor",
        "module_size_gb", "modules_count", "frequency_mhz",
    ],
    "storage": [
        "storage_type", "form_factor", "interface", "capacity_gb",
    ],
    "cooler": [
        "supported_sockets", "max_tdp_watts",
    ],
    "gpu": [
        "vram_gb", "vram_type", "tdp_watts", "needs_extra_power",
        "video_outputs", "core_clock_mhz", "memory_clock_mhz",
    ],
    # motherboard / case — следующие подэтапы
}

# Экстракторы по категориям. Отсутствующая запись = категория ещё не реализована.
EXTRACTORS = {
    "cpu":     cpu_src.extract,
    "psu":     psu_src.extract,
    "ram":     ram_src.extract,
    "storage": storage_src.extract,
    "cooler":  cooler_src.extract,
    "gpu":     gpu_src.extract,
}


def run_for_category(category: str, *, dry_run: bool = False) -> dict:
    """Прогоняет обогащение для одной категории. Возвращает статистику."""
    if category not in EXTRACTORS or category not in REQUIRED_FIELDS:
        return {
            "category":        category,
            "status":          "not_implemented",
            "total":           0,
            "with_null":       0,
            "processed":       0,
            "updated":         0,
            "errors":          0,
            "field_stats":     {},
            "unfilled_fields": {},
        }

    extract = EXTRACTORS[category]
    required = REQUIRED_FIELDS[category]
    table = CATEGORY_TO_TABLE[category]

    stats = {
        "category":        category,
        "status":          "success",
        "total":           0,
        "with_null":       0,
        "processed":       0,
        "updated":         0,
        "errors":          0,
        "field_stats":     {f: 0 for f in required},
        "unfilled_fields": {f: 0 for f in required},
    }

    cols = ", ".join(["id", "model"] + required)
    where_null = " OR ".join(f"{f} IS NULL" for f in required)

    session = SessionLocal()
    try:
        stats["total"] = session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).scalar() or 0

        rows = session.execute(
            text(f"SELECT {cols} FROM {table} WHERE {where_null} ORDER BY id")
        ).mappings().all()
        stats["with_null"] = len(rows)

        for row in rows:
            stats["processed"] += 1
            row_dict = dict(row)

            try:
                extracted = extract(row_dict["model"])
                # Оставляем только обязательные поля; опциональные игнорируем,
                # даже если экстрактор вдруг их вернёт.
                extracted = {k: v for k, v in extracted.items() if k in required}

                # Для отчёта: какие поля остались NULL и после regex
                for f in required:
                    if row_dict.get(f) is None and f not in extracted:
                        stats["unfilled_fields"][f] += 1

                if dry_run:
                    written = [
                        f for f, ef in extracted.items()
                        if row_dict.get(f) is None and ef.value is not None
                    ]
                else:
                    savepoint = session.begin_nested()
                    try:
                        written = apply_enrichment(
                            session, category, row_dict["id"], extracted, row_dict
                        )
                        savepoint.commit()
                    except Exception as exc:
                        savepoint.rollback()
                        logger.error("id=%d: ошибка записи в БД — %s", row_dict["id"], exc)
                        stats["errors"] += 1
                        continue

                if written:
                    stats["updated"] += 1
                    for f in written:
                        stats["field_stats"][f] += 1

            except Exception as exc:
                logger.error("id=%d: ошибка при обработке — %s", row_dict.get("id"), exc)
                stats["errors"] += 1

        if not dry_run:
            session.commit()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return stats
