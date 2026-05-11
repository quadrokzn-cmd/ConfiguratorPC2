# Тесты email_sender (этап 8.3).
#
# smtplib.SMTP_SSL / SMTP патчатся через unittest.mock — в сеть не ходим.
# Один live-тест помечен @pytest.mark.skip — его можно руками снять для
# ручной проверки с реальным mail.ru (по аналогии с OpenAI live-тестом).

from __future__ import annotations

import smtplib
import socket
from unittest.mock import MagicMock, patch

import pytest

from portal.services.configurator.export import email_sender


# --- helpers -------------------------------------------------------------

def _patch_settings(monkeypatch, *, password: str = "app-pass-xyz"):
    """Ставим реалистичные настройки, чтобы send_email не падал ранним чеком."""
    from app.config import settings
    monkeypatch.setattr(settings, "smtp_host", "smtp.mail.ru")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_use_ssl", True)
    monkeypatch.setattr(settings, "smtp_user", "quadro@quadro.tatar")
    monkeypatch.setattr(settings, "smtp_app_password", password)
    monkeypatch.setattr(settings, "smtp_from_name", "КВАДРО-ТЕХ")


# --- Тесты ---------------------------------------------------------------


def test_send_email_calls_smtp_ssl_login_sendmail(monkeypatch):
    """Успешный путь: SMTP_SSL создан, login/sendmail вызваны с нужными
    аргументами."""
    _patch_settings(monkeypatch)
    smtp_mock = MagicMock()
    ssl_cls = MagicMock()
    ssl_cls.return_value.__enter__.return_value = smtp_mock
    ssl_cls.return_value.__exit__.return_value = False

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        email_sender.send_email(
            to_email="sup@ru",
            subject="Тема",
            body_html="<p>hi</p>",
        )

    ssl_cls.assert_called_once_with("smtp.mail.ru", 465, timeout=15)
    smtp_mock.login.assert_called_once_with("quadro@quadro.tatar", "app-pass-xyz")
    args, kwargs = smtp_mock.sendmail.call_args
    # Либо keyword, либо positional — аккуратно достанем.
    from_addr = kwargs.get("from_addr", args[0] if args else None)
    to_addrs = kwargs.get("to_addrs", args[1] if len(args) > 1 else None)
    msg_str  = kwargs.get("msg", args[2] if len(args) > 2 else None)
    assert from_addr == "quadro@quadro.tatar"
    assert to_addrs == ["sup@ru"]    # без BCC
    assert "Subject:" in msg_str
    assert "html" in msg_str.lower()


def test_send_email_adds_bcc_to_recipients_not_header(monkeypatch):
    """BCC добавлен в sendmail recipients, но заголовок Bcc в теле не появляется."""
    _patch_settings(monkeypatch)
    smtp_mock = MagicMock()
    ssl_cls = MagicMock()
    ssl_cls.return_value.__enter__.return_value = smtp_mock

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        email_sender.send_email(
            to_email="sup@ru",
            subject="X",
            body_html="<p>X</p>",
            bcc="quadro@quadro.tatar",
        )

    args, kwargs = smtp_mock.sendmail.call_args
    to_addrs = kwargs.get("to_addrs", args[1] if len(args) > 1 else None)
    msg_str  = kwargs.get("msg", args[2] if len(args) > 2 else None)
    assert set(to_addrs) == {"sup@ru", "quadro@quadro.tatar"}
    # Заголовка "Bcc:" в тексте письма быть не должно — приватность.
    # Регистронезависимо, потому что MIMEText может что-то менять.
    for line in msg_str.splitlines():
        if line.strip() == "":
            break   # конец заголовков — дальше тело, там совпадения не страшны
        assert not line.lower().startswith("bcc:"), line


def test_send_email_skips_bcc_if_same_as_to(monkeypatch):
    """BCC == To: отправляем один раз, не дублируем адрес в recipients."""
    _patch_settings(monkeypatch)
    smtp_mock = MagicMock()
    ssl_cls = MagicMock()
    ssl_cls.return_value.__enter__.return_value = smtp_mock

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        email_sender.send_email(
            to_email="quadro@quadro.tatar",
            subject="X",
            body_html="X",
            bcc="quadro@quadro.tatar",
        )

    args, kwargs = smtp_mock.sendmail.call_args
    to_addrs = kwargs.get("to_addrs", args[1] if len(args) > 1 else None)
    assert to_addrs == ["quadro@quadro.tatar"]


def test_send_email_auth_error_raises_email_send_error(monkeypatch):
    """SMTPAuthenticationError → EmailSendError с понятным сообщением."""
    _patch_settings(monkeypatch)
    smtp_mock = MagicMock()
    smtp_mock.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b"auth failed"
    )
    ssl_cls = MagicMock()
    ssl_cls.return_value.__enter__.return_value = smtp_mock

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        with pytest.raises(email_sender.EmailSendError) as exc_info:
            email_sender.send_email(
                to_email="sup@ru",
                subject="x",
                body_html="x",
            )
    msg = str(exc_info.value)
    assert "SMTP" in msg
    assert "535" in msg


def test_send_email_timeout_raises_email_send_error(monkeypatch):
    """socket.timeout / TimeoutError → EmailSendError."""
    _patch_settings(monkeypatch)
    ssl_cls = MagicMock(side_effect=socket.timeout("timed out"))

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        with pytest.raises(email_sender.EmailSendError) as exc_info:
            email_sender.send_email(
                to_email="sup@ru",
                subject="x",
                body_html="x",
            )
    assert "сетева" in str(exc_info.value).lower() or "smtp" in str(exc_info.value).lower()


def test_send_email_without_password_fails_immediately(monkeypatch):
    """Без SMTP_APP_PASSWORD модуль не пытается открывать сокет."""
    _patch_settings(monkeypatch, password="")
    ssl_cls = MagicMock()

    with patch("portal.services.configurator.export.email_sender.smtplib.SMTP_SSL", ssl_cls):
        with pytest.raises(email_sender.EmailSendError):
            email_sender.send_email(
                to_email="x@x", subject="x", body_html="x",
            )
    ssl_cls.assert_not_called()


# --- Живой тест (снимите @pytest.mark.skip руками) -----------------------

@pytest.mark.skip(reason="live smtp — запускать вручную с реальным паролем")
def test_live_send_to_self():  # pragma: no cover
    """Отправляет письмо самому себе — полезно для проверки mail.ru.

    Требует заполненный SMTP_APP_PASSWORD в .env. Работает только если
    @pytest.mark.skip снят вручную.
    """
    email_sender.send_email(
        to_email="quadro@quadro.tatar",
        subject="КВАДРО-ТЕХ live smtp test",
        body_html="<p>Если вы это читаете — отправка работает.</p>",
    )
