# Тесты бекапов БД: rotate, perform_backup, UI/доступы, безопасность
# имён файлов (этап 9В.2).
#
# Все взаимодействия с Backblaze B2 замокированы через monkeypatch
# (boto3 не вызывается, сетевых обращений нет). Тесты проверяют:
#   - политику ротации 7/4/6 для daily/weekly/monthly;
#   - поведение perform_backup в зависимости от текущего МСК-дня;
#   - права доступа на /settings/backups (admin / manager / anonymous);
#   - регулярки безопасности имён файлов и tier'ов;
#   - вызов pg_dump с правильными аргументами;
#   - mask_db_url.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from portal.services import backup_service
from tests.test_portal.conftest import extract_csrf


# --- Утилита: фабрика «фейкового» B2 -----------------------------------

class _FakeB2Client:
    """In-memory заменитель boto3 S3-клиента для тестов.
    Хранит объекты как dict[key] = {'Body': bytes, 'LastModified': dt, ...}.
    """

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.put_calls: list[dict] = []
        self.delete_calls: list[str] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, Metadata: dict):
        self.objects[Key] = {
            "Body": Body,
            "LastModified": datetime.now(tz=timezone.utc),
            "Metadata": dict(Metadata),
            "Size": len(Body),
        }
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Metadata": dict(Metadata)})
        return {"ETag": '"fake-etag"'}

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop(Key, None)
        self.delete_calls.append(Key)
        return {}

    def get_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(Key)
        body = self.objects[Key]["Body"]

        class _Body:
            def __init__(self, data):
                self._data = data

            def iter_chunks(self, chunk_size=64 * 1024):
                if not self._data:
                    return
                yield self._data

            def close(self):
                pass

        return {
            "Body": _Body(body),
            "ContentLength": len(body),
        }

    def get_paginator(self, op):
        assert op == "list_objects_v2"
        objects = self.objects

        class _Paginator:
            def paginate(self, *, Bucket: str):
                contents = []
                for k, v in objects.items():
                    contents.append({
                        "Key": k,
                        "Size": v["Size"],
                        "LastModified": v["LastModified"],
                    })
                yield {"Contents": contents}

        return _Paginator()


@pytest.fixture
def fake_b2(monkeypatch):
    """Подменяет _make_b2_client на фейковый. Возвращает экземпляр фейка."""
    fake = _FakeB2Client()
    cfg = backup_service._B2Config(
        endpoint="https://fake.b2",
        bucket="test-bucket",
        key_id="key",
        application_key="secret",
    )
    monkeypatch.setattr(
        backup_service,
        "_make_b2_client",
        lambda config=None: (fake, cfg),
    )
    return fake


def _seed_objects(fake_b2: _FakeB2Client, *, prefix: str, count: int) -> list[str]:
    """Создаёт count объектов в указанном префиксе с разными timestamp'ами,
    чтобы сортировка по last_modified давала предсказуемый порядок."""
    keys: list[str] = []
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    for i in range(count):
        key = f"{prefix}kvadro_tech_2026-04-{i+1:02d}T03-00-00.dump"
        fake_b2.objects[key] = {
            "Body": b"x" * 100,
            # Чем больше i, тем «свежее» объект.
            "LastModified": base + timedelta(days=i),
            "Metadata": {},
            "Size": 100,
        }
        keys.append(key)
    return keys


# --- 1-4. Политика ротации ---------------------------------------------

def test_rotate_keeps_only_last_7_daily(fake_b2):
    """Из 10 daily-объектов остаётся 7 самых свежих."""
    daily_keys = _seed_objects(fake_b2, prefix=backup_service.DAILY_PREFIX, count=10)
    result = backup_service.rotate_backups()
    # 3 самых старых (первые в списке seeding) удаляются.
    assert sorted(result["deleted"]) == sorted(daily_keys[:3])
    # 7 самых свежих остаются.
    remaining = [k for k in fake_b2.objects.keys() if k.startswith(backup_service.DAILY_PREFIX)]
    assert len(remaining) == 7
    assert set(remaining) == set(daily_keys[3:])


def test_rotate_keeps_only_last_4_weekly(fake_b2):
    weekly_keys = _seed_objects(fake_b2, prefix=backup_service.WEEKLY_PREFIX, count=6)
    result = backup_service.rotate_backups()
    assert sorted(result["deleted"]) == sorted(weekly_keys[:2])
    remaining = [k for k in fake_b2.objects.keys() if k.startswith(backup_service.WEEKLY_PREFIX)]
    assert len(remaining) == 4


def test_rotate_keeps_only_last_6_monthly(fake_b2):
    monthly_keys = _seed_objects(fake_b2, prefix=backup_service.MONTHLY_PREFIX, count=8)
    result = backup_service.rotate_backups()
    assert sorted(result["deleted"]) == sorted(monthly_keys[:2])
    remaining = [k for k in fake_b2.objects.keys() if k.startswith(backup_service.MONTHLY_PREFIX)]
    assert len(remaining) == 6


def test_rotate_does_not_delete_across_tiers(fake_b2):
    """Если в daily/ всего 3 файла, weekly/ и monthly/ не должны затрагиваться,
    несмотря на то что они «лишние» — у каждого префикса свой счётчик."""
    _seed_objects(fake_b2, prefix=backup_service.DAILY_PREFIX, count=3)
    weekly_keys = _seed_objects(fake_b2, prefix=backup_service.WEEKLY_PREFIX, count=4)
    monthly_keys = _seed_objects(fake_b2, prefix=backup_service.MONTHLY_PREFIX, count=6)

    result = backup_service.rotate_backups()

    assert result["deleted"] == []
    # Всё на месте.
    assert sum(1 for k in fake_b2.objects if k.startswith(backup_service.DAILY_PREFIX)) == 3
    assert sum(1 for k in fake_b2.objects if k.startswith(backup_service.WEEKLY_PREFIX)) == 4
    assert sum(1 for k in fake_b2.objects if k.startswith(backup_service.MONTHLY_PREFIX)) == 6


# --- 5. list_backups: группировка и сортировка -------------------------

def test_list_backups_returns_grouped_and_sorted(fake_b2):
    daily_keys = _seed_objects(fake_b2, prefix=backup_service.DAILY_PREFIX, count=3)
    weekly_keys = _seed_objects(fake_b2, prefix=backup_service.WEEKLY_PREFIX, count=2)
    items = backup_service.list_backups()

    # Все объекты возвращены.
    assert len(items) == 5
    # Сортировка от новых к старым: первый — самый свежий.
    assert items[0]["last_modified"] >= items[-1]["last_modified"]
    # tier проставлен корректно.
    tiers = {it["key"]: it["tier"] for it in items}
    for k in daily_keys:
        assert tiers[k] == "daily"
    for k in weekly_keys:
        assert tiers[k] == "weekly"


# --- 6-9. perform_backup в разные дни МСК -----------------------------

@pytest.fixture
def stub_pg_dump(monkeypatch):
    """Подменяет make_pg_dump — возвращает фиктивные байты, не запускает subprocess."""
    monkeypatch.setattr(
        backup_service, "make_pg_dump",
        lambda url: b"FAKE_DUMP_BYTES",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")


def _set_now_msk(monkeypatch, dt: datetime):
    """Подменяет _now_msk в backup_service на фиксированную дату.
    dt должен быть aware (с tz)."""
    monkeypatch.setattr(backup_service, "_now_msk", lambda: dt)


def test_perform_backup_uploads_to_daily_only_on_regular_day(
    fake_b2, stub_pg_dump, monkeypatch
):
    """Среда 2026-04-29 — обычный день, только daily/."""
    msk = timezone(timedelta(hours=3))
    _set_now_msk(monkeypatch, datetime(2026, 4, 29, 3, 0, tzinfo=msk))

    result = backup_service.perform_backup()

    assert result["tiers"] == ["daily"]
    daily_keys = [k for k in fake_b2.objects if k.startswith(backup_service.DAILY_PREFIX)]
    weekly_keys = [k for k in fake_b2.objects if k.startswith(backup_service.WEEKLY_PREFIX)]
    monthly_keys = [k for k in fake_b2.objects if k.startswith(backup_service.MONTHLY_PREFIX)]
    assert len(daily_keys) == 1
    assert weekly_keys == []
    assert monthly_keys == []


def test_perform_backup_uploads_to_daily_and_weekly_on_sunday(
    fake_b2, stub_pg_dump, monkeypatch
):
    """Воскресенье 2026-04-26 (weekday==6, day=26) — daily + weekly."""
    msk = timezone(timedelta(hours=3))
    sunday = datetime(2026, 4, 26, 3, 0, tzinfo=msk)
    assert sunday.weekday() == 6
    assert sunday.day != 1
    _set_now_msk(monkeypatch, sunday)

    result = backup_service.perform_backup()

    assert result["tiers"] == ["daily", "weekly"]
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.DAILY_PREFIX)]) == 1
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.WEEKLY_PREFIX)]) == 1
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.MONTHLY_PREFIX)]) == 0


def test_perform_backup_uploads_to_daily_and_monthly_on_first_of_month(
    fake_b2, stub_pg_dump, monkeypatch
):
    """1-е число (не воскресенье): daily + monthly. 2026-04-01 — среда."""
    msk = timezone(timedelta(hours=3))
    first = datetime(2026, 4, 1, 3, 0, tzinfo=msk)
    assert first.day == 1
    assert first.weekday() != 6  # 2026-04-01 — среда
    _set_now_msk(monkeypatch, first)

    result = backup_service.perform_backup()

    assert result["tiers"] == ["daily", "monthly"]
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.DAILY_PREFIX)]) == 1
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.WEEKLY_PREFIX)]) == 0
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.MONTHLY_PREFIX)]) == 1


def test_perform_backup_uploads_all_three_when_first_of_month_is_sunday(
    fake_b2, stub_pg_dump, monkeypatch
):
    """Редкий, но валидный кейс: 1-е число + воскресенье. 2026-02-01 — воскресенье."""
    msk = timezone(timedelta(hours=3))
    first_sunday = datetime(2026, 2, 1, 3, 0, tzinfo=msk)
    assert first_sunday.day == 1
    assert first_sunday.weekday() == 6
    _set_now_msk(monkeypatch, first_sunday)

    result = backup_service.perform_backup()

    assert result["tiers"] == ["daily", "weekly", "monthly"]
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.DAILY_PREFIX)]) == 1
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.WEEKLY_PREFIX)]) == 1
    assert len([k for k in fake_b2.objects if k.startswith(backup_service.MONTHLY_PREFIX)]) == 1


# --- 10-12. UI/доступы на GET /settings/backups ---------------------------

def test_admin_can_get_backups_page(admin_portal_client, fake_b2):
    r = admin_portal_client.get("/settings/backups")
    assert r.status_code == 200
    assert "Резервные копии БД" in r.text


def test_manager_cannot_get_backups_page(manager_portal_client):
    r = manager_portal_client.get("/settings/backups")
    # require_admin → HTTPException 403.
    assert r.status_code == 403


def test_anonymous_cannot_get_backups_page(portal_client):
    r = portal_client.get("/settings/backups")
    # LoginRequiredRedirect → 302 на /login.
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# --- 13. POST /settings/backups/create требует admin ----------------------

def test_create_backup_endpoint_blocks_anonymous(portal_client):
    """Анонимный POST → require_admin поднимает LoginRequiredRedirect → 302."""
    r = portal_client.post("/settings/backups/create", data={"csrf_token": "x"})
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


def test_create_backup_endpoint_blocks_manager(manager_portal_client):
    """Менеджер → require_admin поднимает HTTPException 403 раньше CSRF-проверки."""
    # CSRF берём со страницы, доступной менеджеру (/), чтобы убедиться что
    # дело не в плохом токене, а именно в роли.
    r = manager_portal_client.get("/")
    assert r.status_code == 200
    token = extract_csrf(r.text)
    r2 = manager_portal_client.post(
        "/settings/backups/create", data={"csrf_token": token},
    )
    assert r2.status_code == 403


# --- 14. Path traversal в /settings/backups/download/.../... -------------

@pytest.mark.parametrize("tier,filename", [
    ("daily", "../etc/passwd"),
    ("daily", "../config.dump"),
    ("daily", "kvadro_tech_../something.dump"),
    ("daily", "kvadro_tech_2026-04-28T03-00-00.dump.zip"),  # неверное расширение
    ("daily", "evil.dump"),                                   # не наш префикс
    ("invalid", "kvadro_tech_2026-04-28T03-00-00.dump"),     # неизвестный tier
])
def test_download_endpoint_path_traversal_blocked(
    admin_portal_client, fake_b2, tier, filename
):
    r = admin_portal_client.get(f"/settings/backups/download/{tier}/{filename}")
    # Любой такой запрос должен быть отбит 400/404 — не 200.
    assert r.status_code in (400, 404)


# --- 15. mask_db_url ---------------------------------------------------

def test_mask_db_url_hides_password():
    url = "postgresql://user:supersecret@host:5432/dbname"
    masked = backup_service.mask_db_url(url)
    assert "supersecret" not in masked
    assert "****" in masked
    assert "user" in masked
    assert "host:5432/dbname" in masked


def test_mask_db_url_supports_postgres_scheme():
    url = "postgres://u:p@h/db"
    masked = backup_service.mask_db_url(url)
    assert "p@h" not in masked  # пароль не торчит
    assert "****" in masked


def test_mask_db_url_handles_empty_and_no_match():
    assert backup_service.mask_db_url("") == ""
    # URL без пароля — возвращается без изменений (нет смены).
    no_pwd = "postgresql://localhost/db"
    assert backup_service.mask_db_url(no_pwd) == no_pwd


# --- 16-17. pg_dump: правильные аргументы и обработка ошибок -----------

def test_make_pg_dump_runs_correct_command(monkeypatch):
    """Через monkeypatch subprocess.run проверяем переданные аргументы."""
    captured: dict = {}

    class _Result:
        returncode = 0
        stdout = b"DUMP-DATA"
        stderr = b""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return _Result()

    monkeypatch.setattr(backup_service.subprocess, "run", _fake_run)
    monkeypatch.setattr(backup_service, "_resolve_pg_dump_binary", lambda: "/usr/bin/pg_dump")

    out = backup_service.make_pg_dump("postgresql://user:pass@host/db")
    assert out == b"DUMP-DATA"
    assert captured["cmd"][0] == "/usr/bin/pg_dump"
    assert "--format=custom" in captured["cmd"]
    assert "--no-owner" in captured["cmd"]
    assert "--no-acl" in captured["cmd"]
    assert "postgresql://user:pass@host/db" in captured["cmd"]
    assert captured["kwargs"]["capture_output"] is True


def test_make_pg_dump_raises_on_failure(monkeypatch):
    """Если pg_dump возвращает rc!=0, поднимаем RuntimeError; пароль
    из stderr в сообщение об ошибке не должен попасть."""

    class _Result:
        returncode = 1
        stdout = b""
        stderr = b"connection failed for postgresql://user:secretpw@host/db"

    monkeypatch.setattr(backup_service.subprocess, "run", lambda *a, **k: _Result())
    monkeypatch.setattr(backup_service, "_resolve_pg_dump_binary", lambda: "/usr/bin/pg_dump")

    with pytest.raises(RuntimeError) as exc_info:
        backup_service.make_pg_dump("postgresql://user:secretpw@host/db")

    msg = str(exc_info.value)
    assert "secretpw" not in msg
    assert "rc=1" in msg
