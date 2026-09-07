from hypothesis import given, strategies as st

from shared.domain import (
    ActionProposed,
    CreateUserParameters,
    DecisionOutcomeAdapter,
    PersonCandidate,
    validate_person_candidate,
)


@given(st.sampled_from(["test", "тест", "asdf", "null", "none", "-"]))
def test_person_validator_rejects_stopwords(value: str):
    result = validate_person_candidate(PersonCandidate(surname=value, name="Иван"))
    assert not result.valid
    assert any(error.code == "stopword" for error in result.errors)


@given(
    st.text(
        alphabet=st.characters(whitelist_categories=("Nd", "P")),
        min_size=1,
        max_size=20,
    )
)
def test_person_validator_rejects_numeric_and_punctuation_noise(value: str):
    result = validate_person_candidate(PersonCandidate(surname=value, name="Иван"))
    assert not result.valid


def test_decision_outcome_round_trip_and_forbids_extra_fields():
    outcome = ActionProposed(
        rule_key="credentials.user_creation",
        rule_version="2",
        outcome_key="create_user_proposed",
        action="create_user",
        parameters=CreateUserParameters(
            surname="Иванов",
            name="Иван",
            company="Компания",
            department="ИТ",
            title="Инженер",
        ),
        risk_level=2,
    )
    encoded = outcome.model_dump_json()
    assert DecisionOutcomeAdapter.validate_json(encoded) == outcome
