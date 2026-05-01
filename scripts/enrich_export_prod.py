# Wrapper-скрипт выгрузки batch-файлов AI-обогащения прямо из ПРОД-БД
# через railway ssh (этап 11.6.2.3.3, техдолг #6).
#
# Зачем: scripts/enrich_export.py выгружает из локальной БД, AI заполняет,
# scripts/enrich_import.py пытается применить на проде. ID компонентов
# на локали и проде расходятся (разный порядок INSERT в скелеты при
# загрузке прайсов) → теряется ~25–30% match-rate. Этот скрипт устраняет
# перекос: запускает enrich_export.py с --stdout прямо на проде через
# railway ssh, забирает JSON и раскладывает batch-файлы локально в
# enrichment/pending/<category>/ — с прод-id'шниками.
#
# TCP-проксирование прод-БД ВЫКЛЮЧЕНО и открывать его не нужно: всё
# исполняется внутри прод-контейнера, наружу выходит только JSON через
# stdout SSH-сессии.
#
# Пример запуска:
#   python scripts/enrich_export_prod.py --category cooler --batch-size 30
#   python scripts/enrich_export_prod.py --category gpu --batch-size 30 --limit 60
#   python scripts/enrich_export_prod.py --category case --force
#
# Дальнейший workflow:
#   1) Этот скрипт кладёт batch'и с прод-id в enrichment/pending/<cat>/.
#   2) Чаты Claude Code заполняют поля, складывают в enrichment/done/<cat>/.
#   3) python scripts/enrich_import.py --category <cat> --keep-source
#        — sanity-импорт локально, файлы остаются в done/.
#   4) (на машине разработчика, через railway ssh)
#      cat enrichment/done/<cat>/batch_*.json | <upload to prod> &&
#      railway ssh -- python -m scripts.enrich_import --category <cat>
#        — финальный импорт на прод.

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

# Гарантируем, что корень проекта в sys.path (для возможных будущих
# импортов из app.* — сейчас не нужен, но соблюдаем конвенцию остальных
# скриптов).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("enrich_export_prod")

_REPO_ROOT = Path(__file__).resolve().parent.parent
ENRICHMENT_ROOT = _REPO_ROOT / "enrichment"

# Параметры подключения к прод-контейнеру через railway CLI. Совпадают
# с теми, что используются в README/деплойной инструкции; вынесены в
# константы, чтобы при смене сервиса/ключа правка была в одном месте.
RAILWAY_SERVICE = "ConfiguratorPC2"
RAILWAY_SSH_KEY = Path.home() / ".ssh" / "id_ed25519_railway"


def _build_remote_cmd(
    *, category: str, batch_size: int, limit: int | None,
) -> list[str]:
    """Команда, которая исполнится внутри прод-контейнера через railway ssh.
    Запускаем модулем (python -m), чтобы корректно сработал sys.path."""
    cmd = [
        "python", "-m", "scripts.enrich_export",
        "--category", category,
        "--batch-size", str(batch_size),
        "--stdout",
    ]
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    return cmd


def _build_railway_cmd(remote_cmd: list[str]) -> list[str]:
    """Полная команда: railway ssh -s <service> -i <key> -- <remote_cmd...>."""
    return [
        "railway", "ssh",
        "-s", RAILWAY_SERVICE,
        "-i", str(RAILWAY_SSH_KEY),
        "--",
        *remote_cmd,
    ]


def _ensure_railway_available() -> None:
    """Проверяем, что бинарь railway виден в PATH. Иначе — понятная ошибка
    вместо невнятного FileNotFoundError из subprocess."""
    if shutil.which("railway") is None:
        sys.stderr.write(
            "ERROR: railway CLI не найден в PATH.\n"
            "Установите: https://docs.railway.app/develop/cli\n"
        )
        sys.exit(1)


def _parse_remote_json(stdout_bytes: bytes) -> dict:
    """Декодирует stdout SSH-сессии как UTF-8 JSON-документ. Если внутри
    оказался не-JSON (напр., приветственный баннер шелла, ошибки) —
    падаем с понятной ошибкой и фрагментом полученных данных."""
    text = stdout_bytes.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        snippet = text[:1000]
        sys.stderr.write(
            "ERROR: stdout удалённого процесса не является валидным JSON.\n"
            f"  json.JSONDecodeError: {exc}\n"
            "  Первые 1000 символов полученного stdout:\n"
            f"---\n{snippet}\n---\n"
        )
        sys.exit(1)


def _check_pending_clean(category: str, force: bool) -> None:
    """pending/<category>/ должен быть пуст; иначе — предупреждение, и
    --force чтобы перезаписать. Это снижает риск случайно стереть
    батчи, ещё не отправленные в чаты Claude Code."""
    pending_dir = ENRICHMENT_ROOT / "pending" / category
    if not pending_dir.exists():
        return
    existing = list(pending_dir.glob("batch_*.json"))
    if not existing:
        return
    sys.stderr.write(
        f"WARNING: enrichment/pending/{category}/ уже содержит "
        f"{len(existing)} batch-файл(ов):\n"
    )
    for p in existing[:10]:
        sys.stderr.write(f"  - {p.name}\n")
    if len(existing) > 10:
        sys.stderr.write(f"  … ещё {len(existing) - 10}\n")
    if not force:
        sys.stderr.write(
            "Запустите с --force, чтобы продолжить (новые batch-файлы будут "
            "добавлены рядом; одноимённые будут перезаписаны).\n"
        )
        sys.exit(1)
    sys.stderr.write("--force указан, продолжаем.\n")


def _write_batches(document: dict, category: str) -> tuple[int, int]:
    """Раскладывает batches[] из документа по файлам в pending/<category>/.

    Возвращает (число файлов, число items суммарно). Поля верхнего
    уровня документа (target_fields, case_psu_pass) переносятся в
    payload каждого файла, чтобы импортёр получил такую же структуру,
    как при локальной выгрузке через enrich_export.py без --stdout.
    """
    pending_dir = ENRICHMENT_ROOT / "pending" / category
    pending_dir.mkdir(parents=True, exist_ok=True)

    target_fields = document.get("target_fields") or []
    case_psu_pass = bool(document.get("case_psu_pass"))

    files_written = 0
    items_total = 0
    for entry in document.get("batches") or []:
        fname = entry["filename"]
        items = entry.get("items") or []
        file_payload = {
            "category":      category,
            "batch_id":      entry.get("batch_id") or fname.replace(".json", ""),
            "generated_at":  entry.get("generated_at"),
            "target_fields": target_fields,
            "case_psu_pass": case_psu_pass,
            "items":         items,
        }
        out_path = pending_dir / fname
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(file_payload, f, ensure_ascii=False, indent=2)
        files_written += 1
        items_total += len(items)
    return files_written, items_total


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Выгрузка batch-файлов AI-обогащения из ПРОД-БД через "
            "railway ssh (этап 11.6.2.3.3)."
        ),
    )
    parser.add_argument(
        "--category", required=True,
        help="Категория для обогащения (cpu|gpu|case|psu|...).",
    )
    parser.add_argument(
        "--batch-size", type=int, default=30,
        help="Сколько позиций в одном batch-файле (default 30).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Опциональный лимит на число позиций суммарно (smoke-тест).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Не падать, если pending/<category>/ уже содержит batch-файлы.",
    )
    args = parser.parse_args()

    _ensure_railway_available()
    _check_pending_clean(args.category, force=args.force)

    remote_cmd = _build_remote_cmd(
        category=args.category, batch_size=args.batch_size, limit=args.limit,
    )
    railway_cmd = _build_railway_cmd(remote_cmd)
    sys.stderr.write(f"Запускаю на проде: {' '.join(remote_cmd)}\n")

    proc = subprocess.run(
        railway_cmd, capture_output=True, check=False,
    )

    # stderr из удалённого процесса прокидываем в наш stderr как есть
    # (там логи и progress enrich_export.py).
    if proc.stderr:
        try:
            sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
        except Exception:
            sys.stderr.buffer.write(proc.stderr)

    if proc.returncode != 0:
        sys.stderr.write(
            f"ERROR: railway ssh завершился с кодом {proc.returncode}.\n"
        )
        return 1

    document = _parse_remote_json(proc.stdout)
    files_written, items_total = _write_batches(document, args.category)
    sys.stderr.write(
        f"Exported {files_written} batches from PROD → "
        f"enrichment/pending/{args.category}/ ({items_total} items total)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
