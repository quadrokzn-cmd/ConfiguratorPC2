"""Локальная диагностика мусора в таблице cases (этап 11.6.2.4.0).

Не входит в коммит — запускается ручками для понимания состава.
Сохраняет отчёт в scripts/reports/case_audit_<ts>.txt (gitignored).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text


def main() -> int:
    eng = create_engine(
        os.environ['DATABASE_URL'], future=True,
        connect_args={'client_encoding': 'utf8'},
    )

    ts = datetime.now().strftime('%Y%m%dT%H%M%SZ')
    out = Path(__file__).resolve().parent / 'reports' / f'case_audit_{ts}.txt'
    out.parent.mkdir(parents=True, exist_ok=True)
    buf: list[str] = []

    def w(*a):
        s = ' '.join(str(x) for x in a)
        print(s)
        buf.append(s)

    with eng.connect() as c:
        total = c.execute(
            text('SELECT COUNT(*) FROM cases WHERE is_hidden=false')
        ).scalar()
        w(f'== Видимых cases: {total} ==')

        r = c.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE supported_form_factors IS NULL) AS f,
              COUNT(*) FILTER (WHERE has_psu_included IS NULL) AS h,
              COUNT(*) FILTER (WHERE included_psu_watts IS NULL) AS p,
              COUNT(*) FILTER (WHERE supported_form_factors IS NULL
                               AND has_psu_included IS NULL) AS fh,
              COUNT(*) FILTER (
                WHERE supported_form_factors IS NULL
                   OR has_psu_included IS NULL
                   OR included_psu_watts IS NULL
              ) AS any_null
            FROM cases WHERE is_hidden = false
        """)).first()
        w(f'NULL form_factors: {r.f}')
        w(f'NULL has_psu: {r.h}')
        w(f'NULL included_psu_watts: {r.p}')
        w(f'оба NULL (form_factors+has_psu): {r.fh}')
        w(f'любой NULL: {r.any_null}')

        w('\n== Топ-50 брендов среди NULL form_factors ==')
        rows = c.execute(text("""
            SELECT manufacturer, COUNT(*) AS n FROM cases
            WHERE is_hidden=false AND supported_form_factors IS NULL
            GROUP BY manufacturer ORDER BY n DESC LIMIT 50
        """)).all()
        for r in rows:
            w(f'  {(r.manufacturer or "(none)"):30s} {r.n}')

        w('\n== ВСЕ бренды среди видимых cases (полный список) ==')
        rows = c.execute(text("""
            SELECT manufacturer, COUNT(*) AS n FROM cases
            WHERE is_hidden=false
            GROUP BY manufacturer ORDER BY n DESC
        """)).all()
        for r in rows:
            w(f'  {(r.manufacturer or "(none)"):30s} {r.n}')

        w('\n== Маркеры мусора (по cases.model + supplier_prices.raw_name) ==')
        patterns = {
            'drive_cage':
                r'(корзин|салазк|\bcage\b|drive bay|mobile rack|'
                r'mobile-rack|hot.?swap|backplane)',
            'dust_filter':
                r'(пылев|dust filter|противопыл)',
            'slot_cover':
                r'(заглушк|slot cover|slot bracket|зашивка слот)',
            'side_panel_only':
                r'(сменн[аыо]\w+\s+(?:боков|панел)|боковая панель|'
                r'side panel|window panel|tempered\s*glass\s*panel|'
                r'replacement panel)',
            'pcie_riser':
                r'(райзер|\briser\b|pcie\s*extender|pci-e\s*extender|'
                r'riser cable|riser card)',
            'gpu_support':
                r'(gpu\s*support|video\s*card\s*holder|антипровис|'
                r'sag\s*bracket|graphics\s*card\s*holder|'
                r'видеокарт\w+\s+(?:стойк|кроншт|поддерж|опор))',
            'case_accessory':
                r'(\bподставк|\bручк[аи]\b|\bорганайзер|cable\s*tie|'
                r'\bстяжк|carry\s*handle|кабельн\w+\s*стяжк)',
            'storage_in_cases':
                r'(\bhdd\b|\bssd\b|\bжёстк|\bжестк|nvme\b|\bm\.2\b)',
            'fan_only':
                r'(\bfan\b|вентилятор|\bcooler\b|кулер)',
        }
        for name, pat in patterns.items():
            rows = c.execute(text("""
                SELECT c.id, c.manufacturer, c.model,
                       (SELECT string_agg(DISTINCT sp.raw_name, ' || ')
                        FROM supplier_prices sp
                        WHERE sp.component_id = c.id AND sp.category='case'
                       ) AS raw_names,
                       c.supported_form_factors,
                       c.has_psu_included,
                       c.included_psu_watts
                FROM cases c
                WHERE c.is_hidden=false
                  AND (
                    c.model ~* :pat
                    OR EXISTS (
                      SELECT 1 FROM supplier_prices sp
                      WHERE sp.component_id = c.id AND sp.category='case'
                        AND sp.raw_name ~* :pat
                    )
                  )
                ORDER BY c.id
            """), {'pat': pat}).all()
            w(f'\n--- pattern={name} ({len(rows)}) ---')
            for r in rows[:60]:
                rn = (r.raw_names or '')[:140]
                ff = r.supported_form_factors
                w(f'  id={r.id} mfg={r.manufacturer} '
                  f'ff={ff} hp={r.has_psu_included} '
                  f'model={(r.model or "")[:80]}')
                if rn:
                    w(f'    raw={rn}')
            if len(rows) > 60:
                w(f'  ... ещё {len(rows) - 60}')

    out.write_text('\n'.join(buf), encoding='utf-8')
    print(f'\nReport saved: {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
