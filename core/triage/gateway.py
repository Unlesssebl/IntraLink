"""Deterministic Relevance Gateway for Helpdesk incoming tickets.

Enforces Filter #1:
Identifies 100% irrelevant tickets (EDS/Bank-Clients and 1C enterprise bases submitted
to wrong catalog roots) and prepares automatic cancellation outcomes (Status 30)
with prescribed redirect instructions.
"""

import logging
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from core.intraservice.catalog import (
    EDS_SECTION_ID,
    SECTION_1C_ID,
)
from core.intraservice.dto import TaskDTO

logger = logging.getLogger("core.triage.gateway")

# Section 09: EDS & Bank-Client service IDs
EDS_SERVICE_IDS = {24, 30, 31, 224, 227, 231}

# Section 06: 1C ERP & Enterprise DB service IDs
SECTION_1C_SERVICE_IDS = {15, 26, 27, 28, 29, 39, 45, 46, 47, 48, 50, 51, 52, 185}

# 1. EDS / CryptoPro / Bank-Client patterns
EDS_PATTERNS = [
    re.compile(r"\bэцп\b", re.IGNORECASE),
    re.compile(r"\b(?:криптопро|cryptopro|рутокен|rutoken)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:сертификат\s+эцп|сертификат\s+подписи|электронн(?:ая|ой|ую)\s+подпис[ьия])\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:банк-клиент|клиент-банк|сбербанк\s+бизнес|втб\s+бизнес|альфа-бизнес)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:сбис|госуслуг[иа]|диадок|контур|мчд)\b", re.IGNORECASE),
]

# 2. 1C Enterprise database patterns
SECTION_1C_PATTERNS = [
    re.compile(r"\b1[сc]:\s*предприяти[ея]\b", re.IGNORECASE),
    re.compile(r"\b1[сc]\b", re.IGNORECASE),
    re.compile(r"\b(?:упп|зуп|erp)\b", re.IGNORECASE),
    re.compile(r"\bбухгалтери[яие]\s+1[сc]\b", re.IGNORECASE),
    re.compile(r"\b(?:баз[аые]\s+1[сc]|кэш\s+1[сc]|ошибк[аи]\s+1[сc])\b", re.IGNORECASE),
    re.compile(
        r"\b(?:не\s+проводится\s+документ|провести\s+документ|заблокирована\s+таблица|закрытие\s+месяца|открытие\s+периода)\b",
        re.IGNORECASE,
    ),
]

# Exceptions where 1C mention does NOT mean Section 06 problem (1st-line hardware/network issues)
SECTION_1C_EXCEPTIONS = [
    # Network infra issues
    re.compile(
        r"\b(?:коммутатор|свитч|switch|роутер|пинг|ping|потер[яи]\s+пакетов|нет\s+сети|обрыв\s+сети|сетевой\s+кабель|патч-корд)\b",
        re.IGNORECASE,
    ),
    # Peripheral / Printer issues
    re.compile(
        r"\b(?:принтер|мфу|сканер|картридж|тонер|замяти[ея]\s+бумаги|не\s+печатает|печать\s+на\s+пол\s+листа)\b",
        re.IGNORECASE,
    ),
    # General PC freeze / OS issues
    re.compile(
        r"\b(?:компьютер\s+работает\s+медленно|тормозит\s+компьютер|зависает\s+компьютер|зависает\s+пк|переустановка\s+(?:windows|ос|виндовс))\b",
        re.IGNORECASE,
    ),
    # SMB File lock
    re.compile(
        r"\b(?:занят\s+другим|занята\s+другим|заблокирован\s+другим|кем-то\s+занят)\b",
        re.IGNORECASE,
    ),
]


class RelevanceDecision(BaseModel):
    """Decision produced by RelevanceGateway."""

    is_irrelevant: bool
    reason: str = ""
    target_service_id: Optional[int] = None
    target_service_name: str = ""
    public_comment: str = ""
    internal_note: str = ""
    matched_markers: List[str] = Field(default_factory=list)


class RelevanceGateway:
    """Deterministic Filter #1 for immediate cancellation and redirect of irrelevant tickets."""

    def is_eds_service(self, service_id: Optional[int]) -> bool:
        """Check if ticket is already assigned to Section 09 (EDS)."""
        if service_id is None:
            return False
        return service_id in EDS_SERVICE_IDS or service_id == EDS_SECTION_ID

    def is_1c_service(self, service_id: Optional[int]) -> bool:
        """Check if ticket is already assigned to Section 06 (1C)."""
        if service_id is None:
            return False
        return service_id in SECTION_1C_SERVICE_IDS or service_id == SECTION_1C_ID

    def evaluate(self, task: TaskDTO) -> RelevanceDecision:
        """Evaluate ticket text and metadata against deterministic rejection rules."""
        name = task.name or ""
        desc = task.description or ""
        full_text = f"{name} {desc}".strip()
        service_id = task.service_id

        # ---------------------------------------------------------
        # Rule 1: Electronic Digital Signature (EDS) & Bank Clients
        # ---------------------------------------------------------
        if not self.is_eds_service(service_id):
            eds_markers = []
            for pat in EDS_PATTERNS:
                m = pat.search(full_text)
                if m:
                    eds_markers.append(m.group(0))

            if eds_markers:
                markers_str = ", ".join(list(dict.fromkeys(eds_markers)))
                public_comment = (
                    "Заявка отменена, так как вопросы выпуска, продления и настройки "
                    "сертификатов ЭЦП, КриптоПро и систем банк-клиент сопровождаются профильной "
                    "службой в разделе «09. Электронная цифровая подпись (ЭЦП)».\n\n"
                    "Пожалуйста, создайте обращение в соответствующем разделе каталога:\n"
                    "https://servicedesk-pub.corporate.loc/Task/Create?serviceid=24"
                )
                internal_note = (
                    f"[ТРИАЖ: ШЛЮЗ РЕЛЕВАНТНОСТИ] Обращение определено как нецелевое (Личные ЭЦП / Сертификаты). "
                    f"Сработавшие маркеры: [{markers_str}]. "
                    f"Заявка автоматически отменена (Статус 30) с регламентным комментарием перенаправления в раздел 09."
                )
                logger.info(
                    "Ticket #%d matched EDS relevance gateway rule (markers: %s). Rejecting.",
                    task.id,
                    markers_str,
                )
                return RelevanceDecision(
                    is_irrelevant=True,
                    reason=f"Нецелевое обращение: ЭЦП / Сертификаты / Банк-клиент ({markers_str})",
                    target_service_id=24,
                    target_service_name="09. Электронная цифровая подпись (ЭЦП)",
                    public_comment=public_comment,
                    internal_note=internal_note,
                    matched_markers=eds_markers,
                )

        # ---------------------------------------------------------
        # Rule 2: 1C Enterprise Databases & Accounting
        # ---------------------------------------------------------
        if not self.is_1c_service(service_id):
            # Check exceptions first: network/printers/PC freeze
            has_exception = any(pat.search(full_text) for pat in SECTION_1C_EXCEPTIONS)

            if not has_exception:
                section_1c_markers = []
                for pat in SECTION_1C_PATTERNS:
                    m = pat.search(full_text)
                    if m:
                        section_1c_markers.append(m.group(0))

                if section_1c_markers:
                    markers_str = ", ".join(list(dict.fromkeys(section_1c_markers)))
                    public_comment = (
                        "Заявка отменена, так как вопросы функционирования и ошибок "
                        "в информационных базах 1С (ERP, УПП, ЗУП, Бухгалтерия) сопровождаются "
                        "отделом сопровождения 1С в разделе «06. Вопросы по 1С».\n\n"
                        "Пожалуйста, создайте обращение в соответствующем разделе каталога:\n"
                        "https://servicedesk-pub.corporate.loc/Task/Create?serviceid=15"
                    )
                    internal_note = (
                        f"[ТРИАЖ: ШЛЮЗ РЕЛЕВАНТНОСТИ] Обращение определено как нецелевое (1С Предприятие / Базы данных). "
                        f"Сработавшие маркеры: [{markers_str}]. "
                        f"Заявка автоматически отменена (Статус 30) с регламентным комментарием перенаправления в раздел 06."
                    )
                    logger.info(
                        "Ticket #%d matched 1C relevance gateway rule (markers: %s). Rejecting.",
                        task.id,
                        markers_str,
                    )
                    return RelevanceDecision(
                        is_irrelevant=True,
                        reason=f"Нецелевое обращение: Вопросы по 1С ({markers_str})",
                        target_service_id=15,
                        target_service_name="06. Вопросы по 1С",
                        public_comment=public_comment,
                        internal_note=internal_note,
                        matched_markers=section_1c_markers,
                    )

        # ---------------------------------------------------------
        # Eligible / Relevant ticket
        # ---------------------------------------------------------
        return RelevanceDecision(
            is_irrelevant=False,
            reason="Заявка релевантна 1-й линии Helpdesk",
        )
