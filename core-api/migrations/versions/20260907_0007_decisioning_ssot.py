"""typed decisioning SSOT and removal of unused database rules

Revision ID: 20260907_0007
Revises: 20260907_0006
"""

import json
import string
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260907_0007"
down_revision: Union[str, None] = "20260907_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
UUID_TYPE = sa.Uuid().with_variant(postgresql.UUID(as_uuid=True), "postgresql")


def upgrade() -> None:
    # Compatibility catalog entries. Existing installations are not empty, so
    # seed_templates_if_empty cannot safely deliver these records.
    op.execute(
        sa.text(
            """
            INSERT INTO triage_templates
                (key, name, category, status_id, status_name, expenses, template_text, is_active)
            SELECT
                'account_details_clarify',
                'Уточнение реквизитов для создания УЗ',
                'clarification', 35, 'Требует уточнения', 5,
                'Добрый день! Для создания учетной записи сотрудника в Active Directory, пожалуйста, укажите ФИО полностью, должность и подразделение ответным комментарием к этой заявке.',
                true
            WHERE NOT EXISTS (
                SELECT 1 FROM triage_templates WHERE key = 'account_details_clarify'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO triage_templates
                (key, name, category, status_id, status_name, expenses, template_text, is_active)
            SELECT
                'create_user_proposed',
                'Создание пользователя AD — требуется подтверждение',
                'action', 27, 'В работе', 10,
                'Реквизиты сотрудника прошли проверку. Создание учетной записи требует подтверждения оператора и успешной верификации в Active Directory.',
                true
            WHERE NOT EXISTS (
                SELECT 1 FROM triage_templates WHERE key = 'create_user_proposed'
            )
            """
        )
    )

    # A clean database has no application-startup seed at migration time. Load
    # the complete packaged catalog before deriving the v2 response catalog;
    # otherwise the two compatibility rows above make the later startup seeder
    # believe that the legacy catalog is already complete.
    templates_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "rules"
        / "templates.json"
    )
    if not templates_path.is_file():
        raise RuntimeError(f"Decision template seed is missing: {templates_path}")
    templates = json.loads(templates_path.read_text(encoding="utf-8"))
    seed_template = sa.text(
        """
        INSERT INTO triage_templates
            (key, name, category, status_id, status_name, expenses, template_text, is_active)
        VALUES
            (:key, :name, :category, :status_id, :status_name, :expenses, :template_text, true)
        ON CONFLICT (key) DO NOTHING
        """
    )
    bind = op.get_bind()
    bind.execute(
        seed_template,
        [
            {
                "key": key,
                "name": item.get("name") or key,
                "category": item.get("category", "in_work"),
                "status_id": int(item.get("status_id", 27)),
                "status_name": item.get("status_name", "В работе"),
                "expenses": int(item.get("expenses", 10)),
                "template_text": item.get("template", ""),
            }
            for key, item in templates.items()
        ],
    )

    op.create_table(
        "response_templates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("required_variables", JSON_TYPE, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_by",
            sa.String(100),
            server_default="system:migration",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version", name="uq_response_template_key_version"),
    )
    op.create_index("ix_response_templates_key", "response_templates", ["key"])
    op.create_index(
        "uq_response_template_active_key",
        "response_templates",
        ["key"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "resolution_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("outcome_key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("outcome_kind", sa.String(24), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=True),
        sa.Column("target_status_id", sa.Integer(), nullable=True),
        sa.Column("status_name", sa.String(100), nullable=True),
        sa.Column("expenses", sa.Integer(), server_default="10", nullable=False),
        sa.Column("action_id", sa.String(64), nullable=True),
        sa.Column("risk_level", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "requires_approval", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_by",
            sa.String(100),
            server_default="system:migration",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome_kind IN ('clarification','action','manual_review','resolution')",
            name="ck_resolution_policy_kind",
        ),
        sa.CheckConstraint(
            "risk_level BETWEEN 0 AND 3", name="ck_resolution_policy_risk"
        ),
        sa.CheckConstraint(
            "target_status_id IS NULL OR target_status_id IN (27,29,30,35,48)",
            name="ck_resolution_policy_status",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"], ["response_templates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outcome_key", "version", name="uq_resolution_policy_key_version"
        ),
    )
    op.create_index(
        "ix_resolution_policies_outcome_key", "resolution_policies", ["outcome_key"]
    )
    op.create_index(
        "uq_resolution_policy_active_key",
        "resolution_policies",
        ["outcome_key"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "command_secret_artifacts",
        sa.Column("id", UUID_TYPE, nullable=False),
        sa.Column("command_id", UUID_TYPE, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["command_id"], ["commands.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", "name", name="uq_command_secret_name"),
    )
    op.create_index(
        "ix_command_secret_artifacts_command_id",
        "command_secret_artifacts",
        ["command_id"],
    )
    op.create_index(
        "ix_command_secret_artifacts_expires_at",
        "command_secret_artifacts",
        ["expires_at"],
    )

    op.execute(
        sa.text(
            """
            INSERT INTO response_templates
                (key, version, name, template_text, required_variables, is_active, created_by)
            SELECT key, 1, name, template_text, '[]', is_active, 'system:migration'
            FROM triage_templates
            """
        )
    )
    rows = bind.execute(
        sa.text("SELECT id, template_text FROM response_templates")
    ).mappings()
    update_template = sa.text(
        "UPDATE response_templates SET template_text = :template_text, "
        "required_variables = :required_variables WHERE id = :id"
    ).bindparams(sa.bindparam("required_variables", type_=JSON_TYPE))
    for row in rows:
        variables = sorted(
            {
                field_name
                for _literal, field_name, _format_spec, _conversion in string.Formatter().parse(
                    row["template_text"]
                )
                if field_name
            }
        )
        converted = row["template_text"]
        for variable in variables:
            converted = converted.replace(
                "{" + variable + "}", "{{ " + variable + " }}"
            )
        bind.execute(
            update_template,
            {
                "id": row["id"],
                "template_text": converted,
                "required_variables": variables,
            },
        )
    op.execute(
        sa.text(
            """
            INSERT INTO resolution_policies
                (outcome_key, version, outcome_kind, template_id, target_status_id,
                 status_name, expenses, action_id, risk_level, requires_approval,
                 is_active, created_by)
            SELECT t.key, 1, 'resolution', rt.id, t.status_id, t.status_name,
                   t.expenses, NULL, 0, true, t.is_active, 'system:migration'
            FROM triage_templates t
            JOIN response_templates rt ON rt.key = t.key AND rt.is_active = true
            WHERE t.key NOT IN ('account_details_clarify', 'create_user_proposed')
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO resolution_policies
                (outcome_key, version, outcome_kind, template_id, target_status_id,
                 status_name, expenses, action_id, risk_level, requires_approval,
                 is_active, created_by)
            SELECT 'account_details_invalid', 1, 'clarification', id, 35,
                   'Требует уточнения', 5, NULL, 0, true, true, 'system:migration'
            FROM response_templates
            WHERE key = 'account_details_clarify' AND is_active = true
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO resolution_policies
                (outcome_key, version, outcome_kind, template_id, target_status_id,
                 status_name, expenses, action_id, risk_level, requires_approval,
                 is_active, created_by)
            SELECT 'create_user_proposed', 1, 'action', id, NULL, NULL, 10,
                   'create_user', 2, true, true, 'system:migration'
            FROM response_templates
            WHERE key = 'create_user_proposed' AND is_active = true
            """
        )
    )

    op.drop_index(op.f("ix_triage_rules_priority"), table_name="triage_rules")
    op.drop_table("triage_rules")


def downgrade() -> None:
    op.create_table(
        "triage_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("conditions_json", JSON_TYPE, nullable=False),
        sa.Column("target_template_key", sa.String(64), nullable=False),
        sa.Column("actions_override_json", JSON_TYPE, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_triage_rules_priority"), "triage_rules", ["priority"])
    op.drop_index(
        "ix_command_secret_artifacts_expires_at", table_name="command_secret_artifacts"
    )
    op.drop_index(
        "ix_command_secret_artifacts_command_id", table_name="command_secret_artifacts"
    )
    op.drop_table("command_secret_artifacts")
    op.drop_index("uq_resolution_policy_active_key", table_name="resolution_policies")
    op.drop_index(
        "ix_resolution_policies_outcome_key", table_name="resolution_policies"
    )
    op.drop_table("resolution_policies")
    op.drop_index("uq_response_template_active_key", table_name="response_templates")
    op.drop_index("ix_response_templates_key", table_name="response_templates")
    op.drop_table("response_templates")
