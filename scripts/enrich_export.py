# CLI-скрипт выгрузки незаполненных позиций в JSON-батчи для Claude Code.
#
# Этап 2.5Б — старт; этап 11.6.2.1 — расширения:
#   - в каждом item теперь есть mpn / gtin / raw_names (массив);
#   - поля, помеченные derived-правилами как not_applicable_*, исключаются;
#   - имена батчей содержат timestamp, чтобы новые batch-файлы не сталкивались
#     по имени со старыми batch_NNN.json;
#   - есть флаг --max-batches для частичной выгрузки и параллельной
#     обработки в нескольких чатах Claude Code.
#
# Этап 11.6.2.3.3 — workflow-улучшения:
#   - --stdout: не пишет файлы в pending/, а сериализует все batch'и
#     одним JSON-документом в stdout. Используется wrapper'ом
#     enrich_export_prod.py для выгрузки прямо из прод-БД через
#     railway ssh, минуя файловую систему контейнера.
#   - --limit: ограничение по числу позиций суммарно (smoke-тест).
#
# Примеры запуска:
#   python scripts/enrich_export.py --category gpu
#   python scripts/enrich_export.py --category gpu --batch-size 30 --max-batches 5
#   python scripts/enrich_export.py --category case --case-psu-pass
#   python scripts/enrich_export.py --all --batch-size 30
#   python scripts/enrich_export.py --category cooler --batch-size 5 --limit 5 --stdout

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой директории
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.enrichment.claude_code.exporter import (
    export_all,
    export_category,
)
from app.services.enrichment.claude_code.schema import ALL_CATEGORIES


def _format_result(result: dict) -> str:
    cat = result.get("category", "?")
    status = result.get("status")
    if status == "unknown_category":
        return f"  {cat:14} неизвестная категория, пропущено"

    pass_marker = "  [2-й прогон: included_psu_watts]" if result.get("case_psu_pass") else ""
    line1 = (
        f"  {cat:14} кандидатов: {result.get('candidates', 0):4}  "
        f"уже выгружены: {result.get('skipped_known', 0):4}  "
        f"not_applicable: {result.get('filtered_not_applicable', 0):4}  "
        f"экспортировано: {result.get('exported', 0):4}  "
        f"батчей: {len(result.get('batches', []))}"
        f"{pass_marker}"
    )
    if result.get("batches"):
        files = ", ".join(result["batches"])
        line2 = f"      файлы: {files}"
        return line1 + "\n" + line2
    return line1


def _emit_stdout_document(results: list[dict], category_label: str) -> None:
    """Сериализует batch-payload'ы из results в stdout одним JSON-документом
    (формат этапа 11.6.2.3.3, см. модульный docstring выше)."""
    batches: list[dict] = []
    target_fields_doc: list[str] | None = None
    case_psu_pass_doc = False
    for r in results:
        if not target_fields_doc and r.get("target_fields"):
            target_fields_doc = list(r["target_fields"])
        if r.get("case_psu_pass"):
            case_psu_pass_doc = True
        for entry in r.get("batch_payloads", []):
            batches.append({
                "filename": entry["filename"],
                "batch_id": entry["payload"]["batch_id"],
                "generated_at": entry["payload"]["generated_at"],
                "items":    entry["payload"]["items"],
            })

    document = {
        "category":      category_label,
        "exported_at":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_fields": target_fields_doc or [],
        "case_psu_pass": case_psu_pass_doc,
        "batches":       batches,
    }
    json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Выгрузка незаполненных позиций в JSON-батчи для Claude Code.",
    )
    parser.add_argument(
        "--category", choices=ALL_CATEGORIES,
        help="Обработать только одну категорию.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Обработать все категории по очереди.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="Сколько позиций в одном batch-файле (по умолчанию — из схемы).",
    )
    parser.add_argument(
        "--max-batches", type=int, default=None,
        help="Максимум batch-файлов за один прогон. Полезно для отладки и "
             "параллельной обработки несколькими чатами Claude Code "
             "(каждый чат берёт свою порцию).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Этап 11.6.2.3.3: ограничение по числу позиций суммарно. "
             "Полезно для smoke-теста --stdout-режима.",
    )
    parser.add_argument(
        "--case-psu-pass", action="store_true",
        help="Только для --category case: запустить 2-й прогон — выгрузить "
             "корпуса с has_psu_included=TRUE и пустым included_psu_watts.",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Этап 11.6.2.3.3: не создавать файлы в enrichment/pending/, "
             "сериализовать все batch'и одним JSON-документом в stdout. "
             "Используется wrapper'ом enrich_export_prod.py для выгрузки "
             "из прод-БД через railway ssh.",
    )
    args = parser.parse_args()

    if not args.category and not args.all:
        parser.error("Укажите --category <cat> или --all.")
    if args.case_psu_pass and args.category != "case":
        parser.error("--case-psu-pass допустим только вместе с --category case.")
    if args.case_psu_pass and args.all:
        parser.error("--case-psu-pass нельзя комбинировать с --all.")
    if args.stdout and args.all:
        parser.error("--stdout требует --category, не поддерживается с --all.")

    # В --stdout-режиме все логи и progress идут в stderr, чтобы не
    # ломать pipe (на stdout — только итоговый JSON-документ).
    log_stream = sys.stderr if args.stdout else sys.stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        stream=log_stream,
    )

    info_stream = sys.stderr if args.stdout else sys.stdout

    print(file=info_stream)
    print("Выгрузка batch-файлов для Claude Code", file=info_stream)
    print("=" * 78, file=info_stream)

    write_files = not args.stdout
    if args.all:
        results = export_all(
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
    else:
        results = [export_category(
            args.category,
            batch_size=args.batch_size,
            case_psu_pass=args.case_psu_pass,
            max_batches=args.max_batches,
            limit=args.limit,
            write_files=write_files,
        )]

    total_exported = 0
    total_filtered = 0
    total_batches = 0
    for r in results:
        print(_format_result(r), file=info_stream)
        total_exported += r.get("exported", 0)
        total_filtered += r.get("filtered_not_applicable", 0)
        total_batches += len(r.get("batches", []))

    print("-" * 78, file=info_stream)
    print(f"Всего экспортировано позиций: {total_exported}", file=info_stream)
    print(f"Всего батчей создано:         {total_batches}", file=info_stream)
    print(f"Отфильтровано not_applicable: {total_filtered}", file=info_stream)

    if args.stdout:
        _emit_stdout_document(
            results, category_label=args.category or "all",
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
