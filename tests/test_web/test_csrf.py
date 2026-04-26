# Минимальные тесты CSRF-защиты POST-форм конфигуратора.
#
# Этап 9Б.1: тесты, которые относились к /admin/users и /logout,
# переехали в tests/test_portal/ — эти роуты теперь только в портале.
# Здесь остался единственный конфигураторный POST с CSRF — /query.

from __future__ import annotations


def test_query_without_csrf_rejected(manager_client):
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "любой", "csrf_token": "wrong"},
    )
    assert r.status_code == 400
