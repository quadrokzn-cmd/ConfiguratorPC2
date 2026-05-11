# SMTP-клиент: отправка HTML-письма одному получателю (этап 8.3).
#
# Минималистичный модуль поверх stdlib smtplib. Поведение:
#   - SSL на 465 (mail.ru) или обычный SMTP (для localhost/dev), выбирается
#     по настройке smtp_use_ssl;
#   - Bcc добавляется в список получателей, но НЕ прописывается в заголовок
#     «Bcc:» письма — поставщик не должен видеть, что у нас есть архивная
#     копия в quadro@quadro.tatar;
#   - любая сетевая/протокольная ошибка заворачивается в EmailSendError
#     с понятным сообщением, чтобы роутер мог логировать её и вернуть в UI.
#
# Для тестов: smtplib.SMTP_SSL / SMTP патчится через unittest.mock; модуль
# не инициализирует подключение при импорте — вся сетевая работа внутри
# send_email().

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr

from shared.config import settings

logger = logging.getLogger(__name__)


class EmailSendError(RuntimeError):
    """Ошибка отправки: оборачивает любое исключение smtplib/сети.

    Сообщение делается человекочитаемым: «Не удалось отправить письмо на
    X@Y: <оригинальная причина>». Роутер кладёт текст в sent_emails.error_message.
    """


def _build_message(
    *,
    to_email: str,
    subject: str,
    body_html: str,
) -> MIMEText:
    """Собирает MIMEText с нужными заголовками.

    Заголовок From использует отображаемое имя SMTP_FROM_NAME; в нём
    допустим не-ASCII (через formataddr — email.utils сам закодирует).
    """
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_user))
    msg["To"] = to_email
    msg["Reply-To"] = settings.smtp_user
    return msg


def _open_smtp() -> smtplib.SMTP:
    """Открывает подключение SMTP или SMTP_SSL по настройкам.

    SSL-режим на 465 — для mail.ru это основной. Если кто-то в тестовой
    среде выставит SMTP_USE_SSL=false — пойдём обычным SMTP (с timeout).
    """
    timeout = 15
    if settings.smtp_use_ssl:
        return smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=timeout,
        )
    return smtplib.SMTP(
        settings.smtp_host, settings.smtp_port, timeout=timeout,
    )


def send_email(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    bcc: str | None = None,
) -> None:
    """Отправляет одно HTML-письмо. При любой ошибке бросает EmailSendError.

    bcc — дополнительный получатель, добавляется в список sendmail, но в
    заголовок «Bcc:» письма НЕ попадает (поставщик его не увидит). Если
    bcc пустой/совпадает с to_email — добавлять не нужно.
    """
    if not settings.smtp_app_password:
        # Ранний чек: нет пароля — нет отправки. Без него .login() даст
        # невнятную 535 — пусть тут будет чёткое сообщение.
        raise EmailSendError(
            "SMTP_APP_PASSWORD не задан. Добавьте в .env и перезапустите сервис."
        )

    recipients: list[str] = [to_email]
    if bcc and bcc.strip() and bcc.strip().lower() != to_email.strip().lower():
        recipients.append(bcc.strip())

    msg = _build_message(
        to_email=to_email,
        subject=subject,
        body_html=body_html,
    )

    try:
        with _open_smtp() as smtp:
            smtp.login(settings.smtp_user, settings.smtp_app_password)
            smtp.sendmail(
                from_addr=settings.smtp_user,
                to_addrs=recipients,
                msg=msg.as_string(),
            )
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailSendError(
            f"SMTP-аутентификация отклонена ({exc.smtp_code}). "
            "Проверьте SMTP_USER и SMTP_APP_PASSWORD."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailSendError(
            f"SMTP-сервер отверг получателя(ей): {list(exc.recipients.keys())}"
        ) from exc
    except smtplib.SMTPException as exc:
        raise EmailSendError(f"Ошибка SMTP при отправке на {to_email}: {exc}") from exc
    except (OSError, TimeoutError) as exc:
        # socket.timeout/ConnectionRefusedError/etc. — всё наследуется от OSError.
        raise EmailSendError(
            f"Сетевая ошибка при отправке на {to_email}: {exc}"
        ) from exc
