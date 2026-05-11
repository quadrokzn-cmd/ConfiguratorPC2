"""Backfill GPU.video_outputs из gpu.model (source='derived_from_name').

Предпосылка: в прайсовых наименованиях регулярно встречаются выходы
(«HDMI, DP*3», «1xHDMI+1xDVI-D+1xVGA» и т.п.). На этапе 2.5Б AI-агент
извлекал эти значения из строки model, но без URL-источника — и
импортер отклонял их как bad_scheme. Значения валидные, их нужно
записать в БД с явной пометкой source='derived_from_name' и
confidence=0.85 (ниже regex=1.0 и claude_code=0.90, но выше null).

Источник значений: enrichment/archive/gpu/batch_*__20260424_*.json —
там у 32 записей есть fields.video_outputs с about:blank.

Политика:
- Записываем только если gpu.video_outputs сейчас NULL.
- source_url=NULL (т.к. источник — имя компонента, не URL).
- Используем normalize_video_outputs для единообразия.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from shared.db import SessionLocal
from portal.services.configurator.enrichment.claude_code.derive import normalize_video_outputs


SOURCE = "derived_from_name"
CONFIDENCE = 0.85

# Папки, где лежат already-archived results с about:blank.
ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "enrichment" / "archive" / "gpu"


def _collect_candidates():
    """Собирает (id, raw_value) пар из archive-файлов, где video_outputs.about:blank."""
    out: dict[int, str] = {}
    for path in sorted(ARCHIVE_DIR.glob("batch_*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        for it in payload.get("items", []):
            cid = it.get("id")
            fields = it.get("fields") or {}
            vo = fields.get("video_outputs")
            if not (isinstance(cid, int) and isinstance(vo, dict)):
                continue
            value = vo.get("value")
            url = vo.get("source_url")
            if value and url == "about:blank":
                # позже кандидаты могут повторяться, берём первое непустое
                out.setdefault(cid, value)
    return out


def main(dry_run: bool = False):
    candidates = _collect_candidates()
    print(f"Найдено кандидатов в archive/gpu: {len(candidates)}")

    session = SessionLocal()
    written = 0
    skipped_not_null = 0
    skipped_bad_normalize = 0
    skipped_no_match = 0
    try:
        for cid, raw in candidates.items():
            normalized = normalize_video_outputs(raw)
            if not normalized:
                skipped_bad_normalize += 1
                continue

            # Проверяем, что поле ещё NULL
            row = session.execute(
                text("SELECT id, video_outputs FROM gpus WHERE id = :id"),
                {"id": cid},
            ).mappings().first()
            if row is None:
                skipped_no_match += 1
                continue
            if row["video_outputs"] is not None:
                skipped_not_null += 1
                continue

            if not dry_run:
                # UPDATE gpu-table
                session.execute(
                    text("UPDATE gpus SET video_outputs = :v WHERE id = :id"),
                    {"v": normalized, "id": cid},
                )
                # upsert в component_field_sources
                session.execute(
                    text(
                        "INSERT INTO component_field_sources "
                        "    (category, component_id, field_name, source, confidence, source_url, updated_at) "
                        "VALUES "
                        "    ('gpu', :id, 'video_outputs', :source, :conf, NULL, NOW()) "
                        "ON CONFLICT (category, component_id, field_name) DO UPDATE SET "
                        "    source     = EXCLUDED.source, "
                        "    confidence = EXCLUDED.confidence, "
                        "    source_url = EXCLUDED.source_url, "
                        "    updated_at = NOW()"
                    ),
                    {"id": cid, "source": SOURCE, "conf": CONFIDENCE},
                )
            written += 1

        if not dry_run:
            session.commit()

        print(f"Записано: {written}")
        print(f"Пропущено (уже не NULL): {skipped_not_null}")
        print(f"Пропущено (normalize=None): {skipped_bad_normalize}")
        print(f"Пропущено (нет id в таблице): {skipped_no_match}")
        if dry_run:
            print("[DRY-RUN] Транзакция не зафиксирована.")

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
