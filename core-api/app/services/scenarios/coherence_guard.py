"""Coherence Guard: detects cross-domain contradictions and semantic drift."""

from __future__ import annotations

import logging
from typing import Any

from app.services.scenarios.base import ScenarioContext
from app.services.scenarios.contracts import CoherenceVerdict, PriorHypothesis

logger = logging.getLogger("core_api.scenarios.coherence_guard")

# Негативные семантические барьеры по сценариям
# Если сценарий X содержит маркеры из списка Y, значит заявка создана не в том разделе.
DOMAIN_NEGATIVE_BARRIERS: dict[str, dict[str, list[str]]] = {
    # Сценарий создания пользователей (Раздел 01)
    "create_user": {
        "03": [  # Оргтехника
            "принтер", "мфу", "картридж", "печать", "замятие", "тонер", "сканер", "сканирован"
        ],
        "06": [  # 1С
            "база 1с", "бухгалтерия корп", "ерп", "зкгу", "1с:предприятие", "ошибка 1с"
        ],
        "04": [  # Сеть / интернет
            "нет интернета", "обрыв кабеля", "не работает wifi", "свисток", "патчкорд"
        ],
    },
    # Сценарий установки принтеров (Раздел 03)
    "install_printer": {
        "01": [  # Учетные записи
            "создать учетную запись", "новый сотрудник", "создать пользователя", "сбросить пароль"
        ],
        "06": [  # 1С
            "база 1с", "обновить 1с", "конфигурация 1с"
        ],
    },
    # Wi-Fi доступ (Раздел 01)
    "grant_wlan": {
        "03": [
            "принтер", "мфу", "картридж"
        ],
        "06": [
            "база 1с", "ерп"
        ],
    },
}


class CoherenceGuard:
    """
    Контролер смысловой согласованности заявки с её каталожным Prior.
    Работает по принципу Negative Barrier: проверяет отсутствие явных противоречий.
    """

    def __init__(
        self,
        barriers: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        self._barriers = barriers or DOMAIN_NEGATIVE_BARRIERS

    def check_coherence(
        self,
        prior: PriorHypothesis,
        context: ScenarioContext,
    ) -> CoherenceVerdict:
        task = context.task
        text_parts = [
            str(task.get("Name") or ""),
            str(task.get("Description") or ""),
        ]
        for c in context.comments:
            if isinstance(c, dict):
                text_parts.append(str(c.get("Comment") or c.get("comment") or ""))

        combined_text = " ".join(text_parts).lower().strip()

        # Если текст пустой или минимальный (например, заполнена только анкета кастомных полей),
        # противоречий быть не может — сценарий согласован.
        if not combined_text or len(combined_text) < 5:
            return CoherenceVerdict(is_coherent=True, reason="minimal_or_empty_text_coherent")

        scenario_barriers = self._barriers.get(prior.scenario_key)
        if not scenario_barriers:
            # Для сценария нет жестких негативных барьеров
            return CoherenceVerdict(is_coherent=True, reason="no_negative_barriers_defined")

        # Проверяем кросс-доменные коллизии
        for target_root, negative_markers in scenario_barriers.items():
            for marker in negative_markers:
                if marker in combined_text:
                    # Найдена явная коллизия: заявка в разделе X требует функционал Y
                    logger.warning(
                        "CoherenceGuard: cross-domain collision detected for scenario %s: marker '%s' -> target root %s",
                        prior.scenario_key, marker, target_root,
                    )
                    return CoherenceVerdict(
                        is_coherent=False,
                        has_cross_domain_collision=True,
                        divergence_penalty=0.60,
                        collision_target_root=target_root,
                        reason=f"collision_marker:{marker}:target_root_{target_root}",
                    )

        # Конфликтов не обнаружено
        return CoherenceVerdict(
            is_coherent=True,
            has_cross_domain_collision=False,
            divergence_penalty=0.0,
            reason="coherent_with_catalog",
        )
