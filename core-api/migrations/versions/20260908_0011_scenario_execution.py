"""scenario-driven ticket facts and durable event identity

Revision ID: 20260908_0011
Revises: 20260908_0010
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260908_0011"
down_revision: Union[str, None] = "20260908_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")


def upgrade() -> None:
    op.add_column(
        "autopilot_scenarios",
        sa.Column("rollout_mode", sa.String(16), server_default="legacy", nullable=False),
    )
    op.create_check_constraint(
        "ck_autopilot_scenario_rollout_mode",
        "autopilot_scenarios",
        "rollout_mode IN ('legacy','shadow','canary','active')",
    )

    op.add_column("ticket_runs", sa.Column("scenario_key", sa.String(64), nullable=True))
    op.add_column("ticket_runs", sa.Column("scenario_version", sa.Integer(), nullable=True))
    op.add_column(
        "ticket_runs", sa.Column("fact_revision", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("ticket_runs", sa.Column("context_fingerprint", sa.String(64), nullable=True))
    op.add_column("ticket_runs", sa.Column("decision_version", sa.Integer(), nullable=True))
    op.create_index("ix_ticket_runs_scenario_key", "ticket_runs", ["scenario_key"])
    op.create_index("ix_ticket_runs_context_fingerprint", "ticket_runs", ["context_fingerprint"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE ticket_runs SET scenario_key = "
                "COALESCE(trigger_snapshot_json->>'scenario_key', 'legacy') "
                "WHERE scenario_key IS NULL"
            )
        )
    else:
        op.execute(
            sa.text("UPDATE ticket_runs SET scenario_key = 'legacy' WHERE scenario_key IS NULL")
        )

    op.add_column("ticket_run_events", sa.Column("event_key", sa.String(160), nullable=True))
    op.create_unique_constraint(
        "uq_ticket_run_event_key", "ticket_run_events", ["ticket_run_id", "event_key"]
    )

    op.create_table(
        "ticket_fact_observations",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("ticket_run_id", UUID_TYPE, nullable=False),
        sa.Column("fact_key", sa.String(100), nullable=False),
        sa.Column("value_json", JSON_TYPE, nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("evidence_span", sa.Text(), nullable=True),
        sa.Column("sensitivity", sa.String(16), server_default="internal", nullable=False),
        sa.Column("metadata_json", JSON_TYPE, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", UUID_TYPE, nullable=True),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "state IN ('missing','valid','invalid','ambiguous','conflicting','stale')",
            name="ck_ticket_fact_observation_state",
        ),
        sa.CheckConstraint(
            "source_kind IN ('structured_field','directory','diagnostic','comment','parser','llm','operator')",
            name="ck_ticket_fact_observation_source",
        ),
        sa.ForeignKeyConstraint(["ticket_run_id"], ["ticket_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["ticket_fact_observations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ticket_fact_observations_ticket_run_id",
        "ticket_fact_observations",
        ["ticket_run_id"],
    )
    op.create_index(
        "ix_ticket_fact_observations_fact_key", "ticket_fact_observations", ["fact_key"]
    )
    op.create_index(
        "ix_ticket_fact_observations_state", "ticket_fact_observations", ["state"]
    )
    op.create_index(
        "ix_ticket_fact_observations_source_kind",
        "ticket_fact_observations",
        ["source_kind"],
    )
    op.create_index(
        "ix_ticket_fact_observations_observed_at",
        "ticket_fact_observations",
        ["observed_at"],
    )

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                INSERT INTO response_templates
                    (key, version, name, template_text, required_variables,
                     is_active, created_by)
                VALUES
                    ('install_printer_proposed', 1, 'Подготовка установки принтера',
                     'Параметры установки принтера проверены. Выполнение требует подтверждения оператора.',
                     '[]'::jsonb, true, 'system:migration'),
                    ('grant_wlan_proposed', 1, 'Подготовка доступа WLAN',
                     'Учётная запись определена. Предоставление доступа WLAN требует подтверждения оператора.',
                     '[]'::jsonb, true, 'system:migration'),
                    ('resolved_standard', 1, 'Заявка успешно выполнена',
                     'Добрый день! Заявка успешно выполнена. Проверьте, пожалуйста, работоспособность.',
                     '[]'::jsonb, true, 'system:migration:0011')
                ON CONFLICT (key, version) DO NOTHING
                """
            )
        )
        op.execute(
            sa.text(
                """
                INSERT INTO resolution_policies
                    (outcome_key, version, outcome_kind, template_id,
                     target_status_id, status_name, expenses, action_id,
                     risk_level, requires_approval, is_active, created_by)
                SELECT spec.outcome_key, 1, spec.outcome_kind, template.id,
                       spec.target_status_id, spec.status_name, spec.expenses, spec.action_id,
                       spec.risk_level, spec.requires_approval, true, spec.created_by
                FROM (VALUES
                    ('install_printer_proposed', 'action', NULL, NULL, 0, 'install_printer', 1, true, 'system:migration'),
                    ('grant_wlan_proposed', 'action', NULL, NULL, 0, 'grant_wlan', 2, true, 'system:migration'),
                    ('resolved_standard', 'resolution', 29, 'Выполнена', 15, NULL, 0, false, 'system:migration:0011')
                ) AS spec(outcome_key, outcome_kind, target_status_id, status_name, expenses, action_id, risk_level, requires_approval, created_by)
                JOIN response_templates AS template
                  ON template.key = spec.outcome_key AND template.is_active = true
                ON CONFLICT (outcome_key, version) DO NOTHING
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DELETE FROM resolution_policies WHERE "
                "(outcome_key IN ('install_printer_proposed', 'grant_wlan_proposed') AND created_by = 'system:migration') "
                "OR (outcome_key = 'resolved_standard' AND created_by = 'system:migration:0011')"
            )
        )
        op.execute(
            sa.text(
                "DELETE FROM response_templates WHERE "
                "(key IN ('install_printer_proposed', 'grant_wlan_proposed') AND created_by = 'system:migration') "
                "OR (key = 'resolved_standard' AND created_by = 'system:migration:0011')"
            )
        )
    op.drop_table("ticket_fact_observations")
    op.drop_constraint("uq_ticket_run_event_key", "ticket_run_events", type_="unique")
    op.drop_column("ticket_run_events", "event_key")
    op.drop_index("ix_ticket_runs_context_fingerprint", table_name="ticket_runs")
    op.drop_index("ix_ticket_runs_scenario_key", table_name="ticket_runs")
    op.drop_column("ticket_runs", "decision_version")
    op.drop_column("ticket_runs", "context_fingerprint")
    op.drop_column("ticket_runs", "fact_revision")
    op.drop_column("ticket_runs", "scenario_version")
    op.drop_column("ticket_runs", "scenario_key")
    op.drop_constraint(
        "ck_autopilot_scenario_rollout_mode", "autopilot_scenarios", type_="check"
    )
    op.drop_column("autopilot_scenarios", "rollout_mode")
