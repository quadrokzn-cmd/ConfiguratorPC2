# CLI-скрипт для тестирования NLU-модуля (этап 4).
#
# Принимает свободный текст менеджера и печатает финальный ответ.
#
# Примеры:
#   python scripts/query.py --text "нужен игровой ПК до 100к"
#   python scripts/query.py --stdin
#   python scripts/query.py --text "..." --json   # сырой JSON-вывод
#
# В режиме --json выводятся все детали (parsed, request, result, warnings),
# в обычном режиме — отформатированный текст для менеджера.

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# На Windows консоль по умолчанию cp1251 — не умеет печатать ≈, ₽, •.
# Принудительно переводим stdout/stderr в UTF-8 (Python 3.7+).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv
load_dotenv()

from portal.services.configurator.engine.schema import result_to_dict
from portal.services.configurator.nlu.pipeline import process_query
from portal.services.configurator.nlu.schema import FinalResponse


def _response_to_dict(resp: FinalResponse) -> dict:
    """Превращает FinalResponse в JSON-сериализуемый dict."""
    out: dict = {
        "kind":                  resp.kind,
        "interpretation":        resp.interpretation,
        "warnings":              list(resp.warnings),
        "clarifying_questions":  list(resp.clarifying_questions),
        "cost_usd":              round(resp.cost_usd, 6),
    }
    if resp.parsed is not None:
        out["parsed"] = {
            "is_empty":              resp.parsed.is_empty,
            "purpose":               resp.parsed.purpose,
            "budget_usd":            resp.parsed.budget_usd,
            "cpu_manufacturer":      resp.parsed.cpu_manufacturer,
            "overrides":             dict(resp.parsed.overrides),
            "model_mentions":        [
                {"category": m.category, "query": m.query}
                for m in resp.parsed.model_mentions
            ],
            "raw_summary":           resp.parsed.raw_summary,
        }
    if resp.resolved:
        out["resolved"] = [
            {
                "query":         r.mention.query,
                "category":      r.mention.category,
                "found_id":      r.found_id,
                "found_model":   r.found_model,
                "is_substitute": r.is_substitute,
                "note":          r.note,
            }
            for r in resp.resolved
        ]
    if resp.build_request is not None:
        # Не пишем большие dataclasses целиком — только ключевые поля.
        br = resp.build_request
        out["build_request"] = {
            "budget_usd":  br.budget_usd,
            "cpu":         asdict(br.cpu),
            "ram":         asdict(br.ram),
            "gpu":         asdict(br.gpu),
            "storage":     asdict(br.storage),
            "motherboard": asdict(br.motherboard) if br.motherboard else None,
            "case":        asdict(br.case) if br.case else None,
            "psu":         asdict(br.psu) if br.psu else None,
            "cooler":      asdict(br.cooler) if br.cooler else None,
        }
    if resp.build_result is not None:
        out["build_result"] = result_to_dict(resp.build_result)
    return out


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    ap = argparse.ArgumentParser(
        description="Подбор конфигурации ПК по свободному тексту менеджера.",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text",  help="Текст заявки прямо в командной строке")
    src.add_argument("--stdin", action="store_true", help="Читать текст из stdin")
    ap.add_argument("--json",   action="store_true",
                    help="Сырой JSON-вывод (по умолчанию — текст для менеджера)")
    args = ap.parse_args()

    if args.stdin:
        query_text = sys.stdin.read().strip()
    else:
        query_text = (args.text or "").strip()

    if not query_text:
        print("Пустой ввод.", file=sys.stderr)
        return 1

    resp = process_query(query_text)

    if args.json:
        print(json.dumps(_response_to_dict(resp), ensure_ascii=False, indent=2))
    else:
        print(resp.formatted_text)
        # Дополнительно — стоимость вызовов и kind, чтобы оператору было видно.
        print(f"\n[служебно] kind={resp.kind}  стоимость вызовов: ${resp.cost_usd:.4f}")

    # exit-код: 0 если запрос осмыслен и подбор удался, иначе 1
    return 0 if resp.kind in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
