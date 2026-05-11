# Одноразовый (идемпотентный) пересчёт best_candidate_score для всех
# активных записей unmapped_supplier_items (этап 7.1).
#
# Запускается вручную после применения миграции 010:
#   python scripts/recalculate_unmapped_scores.py
#   python scripts/recalculate_unmapped_scores.py --only-missing
#
# Можно повторять: для записей с уже просчитанным score (поле
# best_candidate_calculated_at IS NOT NULL) — пропускать, указав
# --only-missing. Без этого флага — пересчитывает все.

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# На Windows cp1251 не умеет в стрелки и ≈: принудительно UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from shared.db import SessionLocal
# UI-2 (Путь B, 2026-05-11): mapping_service переехал в portal/services/databases.
from portal.services.databases import mapping_service


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="Пересчёт score подозрительности для записей "
                    "unmapped_supplier_items (этап 7.1).",
    )
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Пересчитать только те записи, у которых score ещё не считался.",
    )
    ap.add_argument(
        "--batch",
        type=int,
        default=100,
        help="Размер батча коммита (по умолчанию 100).",
    )
    args = ap.parse_args()

    session = SessionLocal()
    try:
        where = "status IN ('pending', 'created_new')"
        if args.only_missing:
            where += " AND best_candidate_calculated_at IS NULL"
        ids = [
            int(r.id)
            for r in session.execute(
                text(f"SELECT id FROM unmapped_supplier_items WHERE {where} ORDER BY id")
            ).all()
        ]
        total = len(ids)
        print(f"Записей к пересчёту: {total}")
        if total == 0:
            return 0

        t0 = time.time()
        processed = 0
        suspicious = 0
        new_cnt = 0
        for rid in ids:
            try:
                score, _ = mapping_service.recalculate_score(session, rid)
            except Exception as exc:
                logging.error("id=%s: ошибка пересчёта — %s", rid, exc)
                session.rollback()
                continue
            if score >= mapping_service.SCORE_SUSPICIOUS_THRESHOLD:
                suspicious += 1
            else:
                new_cnt += 1
            processed += 1
            if processed % args.batch == 0:
                session.commit()
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = (total - processed) / rate if rate > 0 else 0
                print(
                    f"  обработано {processed}/{total} "
                    f"(suspicious={suspicious}, new={new_cnt}) "
                    f"· {rate:.1f} зап/с · ~{remaining:.0f}с осталось"
                )

        session.commit()
        print()
        print("Готово.")
        print(f"  Обработано:    {processed}")
        print(f"  Подозрительных (score >= {mapping_service.SCORE_SUSPICIOUS_THRESHOLD}): {suspicious}")
        print(f"  Вероятно новых (score <  {mapping_service.SCORE_SUSPICIOUS_THRESHOLD}): {new_cnt}")
        print(f"  Время:         {time.time() - t0:.1f}с")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
