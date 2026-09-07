"""Deterministic person-name validation used on both trust boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.domain.models import PersonCandidate, ValidPerson, ValidationError


STOPWORDS = frozenset(
    {
        "test",
        "тест",
        "asdf",
        "null",
        "none",
        "-",
        "фамилия",
        "имя",
        "отчество",
        "нет",
        "не указано",
    }
)
NAME_RE = re.compile(r"^[А-ЯЁа-яё]+(?:[ -][А-ЯЁа-яё]+)*$")


@dataclass(frozen=True)
class PersonValidationResult:
    person: ValidPerson | None
    errors: tuple[ValidationError, ...]

    @property
    def valid(self) -> bool:
        return self.person is not None and not self.errors


def normalize_person_name(value: str | None) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return "-".join(part.capitalize() for part in cleaned.split("-") if part)


def _validate_name_part(
    field: str, raw_value: str, *, required: bool
) -> tuple[str, list[ValidationError]]:
    raw_clean = re.sub(r"\s+", " ", (raw_value or "").strip())
    value = normalize_person_name(raw_value)
    errors: list[ValidationError] = []
    if raw_clean.casefold() in STOPWORDS:
        errors.append(
            ValidationError(
                field=field, code="stopword", message="Обнаружено служебное значение"
            )
        )
    if not value:
        if required and not errors:
            errors.append(
                ValidationError(
                    field=field,
                    code="missing",
                    message="Обязательное поле не заполнено",
                )
            )
        return value, errors
    if value.casefold() in STOPWORDS and not errors:
        errors.append(
            ValidationError(
                field=field, code="stopword", message="Обнаружено служебное значение"
            )
        )
    letters = sum(ch.isalpha() for ch in value)
    if letters < 2:
        errors.append(
            ValidationError(
                field=field, code="too_short", message="Требуется минимум две буквы"
            )
        )
    if not NAME_RE.fullmatch(value):
        errors.append(
            ValidationError(
                field=field,
                code="invalid_characters",
                message="Допустимы только кириллица, пробел и дефис",
            )
        )
    return value, errors


def validate_person_candidate(candidate: PersonCandidate) -> PersonValidationResult:
    surname, surname_errors = _validate_name_part(
        "surname", candidate.surname, required=True
    )
    name, name_errors = _validate_name_part("name", candidate.name, required=True)
    patronymic, patronymic_errors = _validate_name_part(
        "patronymic", candidate.patronymic, required=False
    )
    errors = tuple(surname_errors + name_errors + patronymic_errors)
    if errors:
        return PersonValidationResult(person=None, errors=errors)
    return PersonValidationResult(
        person=ValidPerson(
            surname=surname,
            name=name,
            patronymic=patronymic,
            title=candidate.title,
            department=candidate.department,
            company=candidate.company,
            phone=candidate.phone,
            pc_name=candidate.pc_name,
        ),
        errors=(),
    )
