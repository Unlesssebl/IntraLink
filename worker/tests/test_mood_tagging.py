"""Unit tests for UI Mood Tagging (detect_tense_tone) and intent analyzer tone integration."""

from core.autopilot.intent import detect_tense_tone
from core.autopilot.dialogue import AntiLoopGuard, UserReplyIntent, UserReplyIntentAnalyzer


def test_detect_tense_tone_urgent_keywords():
    """Verify various urgent and distressed phrases trigger is_tense=True."""
    phrases = [
        "Срочно почините, отчет горит!",
        "СРОЧНО нужен принтер, работа стоит",
        "Шеф ругается, когда уже сделаете??",
        "Сколько можно ждать! Ничего не работает!!",
        "Третий раз пишу, вопрос критично важный",
        "Встала работа всей бухгалтерии, караул!",
        "SOS, помогите asap!",
    ]
    for p in phrases:
        is_tense, reason = detect_tense_tone(p)
        assert is_tense is True, f"Failed for phrase: '{p}'"
        assert reason is not None


def test_detect_tense_tone_calm_messages():
    """Verify standard neutral messages do not trigger is_tense."""
    calm_phrases = [
        "Здравствуйте! Настройте, пожалуйста, сетевой принтер.",
        "Имя компьютера WKS-1042, принтер HP LaserJet.",
        "Спасибо, уже заработало.",
        "Добрый день, подскажите, где посмотреть имя ПК?",
        "Все реквизиты указал в описании заявки.",
    ]
    for p in calm_phrases:
        is_tense, reason = detect_tense_tone(p)
        assert is_tense is False, f"False positive for phrase: '{p}'"
        assert reason is None


def test_intent_analyzer_preserves_intent_with_tense_flag():
    """Verify applicant clarification question with urgent tone is classified properly with is_tense=True."""
    analyzer = UserReplyIntentAnalyzer()

    # Question asked urgently
    res = analyzer.analyze_reply("Срочно подскажите где найти этот WKS!! Начальство ждет отчет!")
    assert res.intent == UserReplyIntent.CLARIFICATION_QUESTION
    assert res.is_tense is True
    assert "Обнаружен маркер" in (res.tense_reason or "")

    # Providing data calmly
    res_data = analyzer.analyze_reply("Здравствуйте, имя компьютера WKS-0042, принтер 10.20.30.40")
    assert res_data.intent == UserReplyIntent.PROVIDE_DATA
    assert res_data.is_tense is False


def test_anti_loop_detects_out_of_office_auto_replies():
    """Verify automated vacation and out-of-office notices are captured by AntiLoopGuard."""
    guard = AntiLoopGuard()
    assert guard.is_auto_reply("Автоматический ответ: нахожусь в отпуске до 10 октября") is True
    assert guard.is_auto_reply("Out of office: I will be away with limited access to email") is True
    assert guard.is_auto_reply("Письмо сгенерировано автоматически, пожалуйста, не отвечайте") is True
    assert guard.is_auto_reply("Здравствуйте, вот имя компьютера WKS-9999") is False
