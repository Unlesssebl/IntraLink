import pytest
from app.services.deduplication import DuplicateDetector, extract_task_hardware, calculate_text_similarity


def test_user_reported_tasks_132770_and_133098_not_duplicate():
    """Проверка реального инцидента пользователя: Акты технического освидетельствования на разные ПК."""
    task_132770 = {
        "Id": 132770,
        "Name": "акт технического освидетельствования",
        "Description": "низкое разрешение экрана монитора",
        "Creator": "Черкасова Наталья",
        "CreatorId": 8009,
        "ServiceId": 57,
        "Created": "2026-06-08T14:13:10.637",
        "_field_meta": {"pc_name": "TKT001382", "inventory_number": "НТЗ001382"},
    }
    task_133098 = {
        "Id": 133098,
        "Name": "акт технического освидетельствования",
        "Description": "низкое разрешение экрана",
        "Creator": "Черкасова Наталья",
        "CreatorId": 8009,
        "ServiceId": 57,
        "Created": "2026-06-10T13:11:27.433",
        "_field_meta": {"pc_name": "NTEMW1123", "inventory_number": "590"},
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([task_132770, task_133098])
    assert len(dups) == 0, "Заявки на разные ПК и разные единицы оборудования не должны быть дубликатами!"


def test_different_pc_hard_veto():
    """Жёсткое вето: заявки на разные ПК никогда не считаются дубликатами."""
    t1 = {
        "Id": 101,
        "Name": "Не включается ПК",
        "Description": "Черный экран при включении",
        "Creator": "Иванов Иван",
        "CreatorId": 501,
        "Created": "2026-06-01T10:00:00",
        "_field_meta": {"pc_name": "KMK0090"},
    }
    t2 = {
        "Id": 102,
        "Name": "Не включается ПК",
        "Description": "Черный экран при включении",
        "Creator": "Иванов Иван",
        "CreatorId": 501,
        "Created": "2026-06-01T10:05:00",
        "_field_meta": {"pc_name": "NTEMW1070"},
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 0, "Заявки на разные рабочие станции не должны объединяться в дубликат!"


def test_different_inventory_hard_veto():
    """Жёсткое вето: разный инвентарный номер означает разное оборудование."""
    t1 = {
        "Id": 201,
        "Name": "Замена картриджа",
        "Description": "Полосит при печати",
        "Creator": "Петров Петр",
        "CreatorId": 502,
        "Created": "2026-06-01T11:00:00",
        "_field_meta": {"inventory_number": "INV-00123"},
    }
    t2 = {
        "Id": 202,
        "Name": "Замена картриджа",
        "Description": "Полосит при печати",
        "Creator": "Петров Петр",
        "CreatorId": 502,
        "Created": "2026-06-01T11:10:00",
        "_field_meta": {"inventory_number": "INV-00999"},
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 0, "Разные инвентарники не могут быть дубликатами!"


def test_time_diff_over_24h_veto():
    """Жёсткое вето: заявки с разницей более 24 часов без совпадения ПК не считаются дабл-кликом."""
    t1 = {
        "Id": 301,
        "Name": "Не работает интернет",
        "Description": "Нет подключения к сети",
        "Creator": "Сидоров Алексей",
        "CreatorId": 503,
        "Created": "2026-06-01T10:00:00",
    }
    t2 = {
        "Id": 302,
        "Name": "Не работает интернет",
        "Description": "Нет подключения к сети",
        "Creator": "Сидоров Алексей",
        "CreatorId": 503,
        "Created": "2026-06-03T12:00:00",  # Разница 50 часов
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 0, "Заявка через 2 дня — это повторный инцидент, а не ошибочный дубликат очереди!"


def test_same_title_different_description_not_duplicate():
    """Заявки с одинаковым шаблонным названием, но разной сутью проблемы не дубликаты."""
    t1 = {
        "Id": 401,
        "Name": "Установка ПО",
        "Description": "Прошу установить 1С:Бухгалтерия 8.3",
        "Creator": "Ковалева Анна",
        "CreatorId": 504,
        "ServiceId": 12,
        "Created": "2026-06-01T10:00:00",
    }
    t2 = {
        "Id": 402,
        "Name": "Установка ПО",
        "Description": "Прошу установить графический редактор AutoCAD",
        "Creator": "Ковалева Анна",
        "CreatorId": 504,
        "ServiceId": 12,
        "Created": "2026-06-01T10:15:00",
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 0, "Одинаковое название услуги при разных программах не должно считаться дубликатом!"


def test_genuine_double_click_duplicate():
    """Истинный дубликат: дабл-клик пользователя с одинаковым текстом за пару минут."""
    t1 = {
        "Id": 501,
        "Name": "Не печатает принтер HP LaserJet",
        "Description": "Застряла бумага в лотке 2",
        "Creator": "Белов Сергей",
        "CreatorId": 505,
        "Created": "2026-06-01T14:00:00",
        "_field_meta": {"pc_name": "KMK0010"},
    }
    t2 = {
        "Id": 502,
        "Name": "Не печатает принтер HP LaserJet",
        "Description": "Застряла бумага в лотке 2",
        "Creator": "Белов Сергей",
        "CreatorId": 505,
        "Created": "2026-06-01T14:02:00",
        "_field_meta": {"pc_name": "KMK0010"},
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 1
    assert dups[0]["master_task_id"] == 501
    assert dups[0]["duplicate_task_id"] == 502
    assert dups[0]["action"]["status_id"] == 30


def test_collective_duplicate_same_pc():
    """Коллективный дубль: разные пользователи отправляют заявку на один и тот же ПК."""
    t1 = {
        "Id": 601,
        "Name": "Завис компьютер",
        "Description": "Синий экран смерти BSOD при входе в Windows",
        "Creator": "Сотрудник 1",
        "CreatorId": 701,
        "Created": "2026-06-01T09:00:00",
        "_field_meta": {"pc_name": "NTEMW1050"},
    }
    t2 = {
        "Id": 602,
        "Name": "Завис компьютер",
        "Description": "Синий экран смерти BSOD при входе в Windows",
        "Creator": "Сотрудник 2",
        "CreatorId": 702,
        "Created": "2026-06-01T09:30:00",
        "_field_meta": {"pc_name": "NTEMW1050"},
    }

    detector = DuplicateDetector()
    dups = detector.find_duplicates([t1, t2])
    assert len(dups) == 1
    assert dups[0]["master_task_id"] == 601
    assert dups[0]["duplicate_task_id"] == 602
