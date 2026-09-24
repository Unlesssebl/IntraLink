"""Tests for core.rag.sanitizer PII masking and log dump truncation."""

from core.rag.sanitizer import (
    PIISanitizer,
    sanitize_text,
    truncate_log_dump,
)


def test_sanitize_phone_numbers():
    raw = "Позвоните заявителю по номеру +7 (999) 123-45-67 или 8-926-111-22-33 для проверки."
    sanitized = sanitize_text(raw)
    assert "[PHONE]" in sanitized
    assert "+7 (999) 123-45-67" not in sanitized
    assert "8-926-111-22-33" not in sanitized
    assert "Позвоните заявителю по номеру [PHONE] или [PHONE] для проверки." == sanitized


def test_sanitize_emails():
    raw = "Заявка от ivan.ivanov@company.ru перенаправлена на helpdesk@corp.lan."
    sanitized = sanitize_text(raw)
    assert "[EMAIL]" in sanitized
    assert "ivan.ivanov@company.ru" not in sanitized
    assert "helpdesk@corp.lan" not in sanitized
    assert sanitized == "Заявка от [EMAIL] перенаправлена на [EMAIL]."


def test_sanitize_credentials_and_tokens():
    raw = (
        "Установлен временный пароль: SecretPassword123!\n"
        "Авторизация через Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN\n"
        "API токен: sec_token_abcdef123456789\n"
        "Служебный pwd=AdminPass2026;"
    )
    sanitized = sanitize_text(raw)
    assert "SecretPassword123!" not in sanitized
    assert "sec_token_abcdef123456789" not in sanitized
    assert "AdminPass2026" not in sanitized
    assert "[PASSWORD]" in sanitized
    assert "[TOKEN]" in sanitized


def test_clean_html_markup():
    raw = "<p>Принтер <b>HP LaserJet</b> не печатает.<br/>Ошибка в очереди печати.&nbsp;Перезапустили spooler.</p>"
    sanitized = sanitize_text(raw)
    assert "<p>" not in sanitized
    assert "<b>" not in sanitized
    assert "<br/>" not in sanitized
    assert "&nbsp;" not in sanitized
    assert "Принтер HP LaserJet не печатает.\nОшибка в очереди печати. Перезапустили spooler." in sanitized


def test_truncate_log_dump():
    # Construct a log dump exceeding 2000 chars
    long_log = "2026-09-24 12:00:00 [ERROR] OutOfMemoryError in service worker.\n" * 50
    assert len(long_log) > 2000

    truncated = truncate_log_dump(long_log, max_chars=2000)
    assert len(truncated) <= 2000
    assert truncated.endswith("... [TRUNCATED LOG DUMP]")


def test_sanitizer_result_detected_types():
    sanitizer = PIISanitizer(max_length=500)
    raw = (
        "Пользователь с email user@test.com и телефоном +79990001122 сообщил пароль: MyPass123.\n"
        + ("Трассировка ошибки:\n" + "Traceback line\n" * 40)
    )
    res = sanitizer.sanitize(raw)
    assert "EMAIL" in res.detected_types
    assert "PHONE" in res.detected_types
    assert "PASSWORD" in res.detected_types
    assert "LOG_DUMP" in res.detected_types
    assert res.is_truncated is True
    assert len(res.sanitized_text) <= 500


def test_preserve_normal_technical_text():
    raw = "Выполнена перезагрузка службы Spooler на WKS-0042 (IP: 192.168.10.45). Проверен порт 9100."
    sanitized = sanitize_text(raw)
    assert "Spooler" in sanitized
    assert "WKS-0042" in sanitized
    assert "192.168.10.45" in sanitized
    assert "9100" in sanitized


def test_shannon_entropy_detection():
    # High-entropy random 32-character secret/token without 'password:' prefix
    high_entropy_token = "4a8bF92cE10d7a6B3fC8e91d0A2b4C6e"
    raw = f"Сгенерирован ключ аутентификации {high_entropy_token} для сервиса."
    sanitized = sanitize_text(raw)
    assert high_entropy_token not in sanitized
    assert "[TOKEN]" in sanitized

