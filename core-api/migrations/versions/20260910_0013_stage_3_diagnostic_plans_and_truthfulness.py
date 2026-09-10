\"\"\"stage 3 diagnostic plans and truthfulness policies

Revision ID: 20260910_0013
Revises: 20260909_0012
\"\"\"

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260910_0013"
down_revision: Union[str, None] = "20260909_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEMPLATES_DATA = [
    (
        "defect_type_clarify",
        1,
        "Уточнение дефекта МФУ",
        "Уточните, пожалуйста, характер дефекта устройства (замятие бумаги, дефекты печати, ошибка на дисплее или замена картриджа), а также место установки аппарата.",
        "clarification",
        35,
        "Ожидание ответа",
        5,
        False,
    ),
    (
        "scan_connection_clarify",
        1,
        "Уточнение типа подключения сканера",
        "Уточните, пожалуйста, тип подключения сканера/МФУ (сетевое подключение или USB-кабель к ПК), а также сетевой адрес устройства или сетевую папку.",
        "clarification",
        35,
        "Ожидание ответа",
        5,
        False,
    ),
    (
        "printer_queue_clarify",
        1,
        "Уточнение параметров очереди печати",
        "Уточните, пожалуйста, имя рабочего компьютера или сетевой адрес принтера, на котором возникла проблема с печатью.",
        "clarification",
        35,
        "Ожидание ответа",
        5,
        False,
    ),
    (
        "install_printer_planned",
        1,
        "План установки принтера",
        "Данные для установки принтера подготовлены. После подтверждения инженером будет выполнена установка.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "printer_hardware_service_planned",
        1,
        "План обслуживания МФУ",
        "Заявка на обслуживание МФУ зарегистрирована. Инженер уточняет условия и необходимость выезда.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "printer_print_failure_planned",
        1,
        "План диагностики очереди печати",
        "Подготовлен план проверки очереди печати и доступности принтера. Результат сообщим после диагностики.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "printer_scan_failure_planned",
        1,
        "План проверки сканирования",
        "Уточняем тип подключения сканера и параметры назначения, после чего выполним подходящую проверку.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "pc_performance_planned",
        1,
        "План диагностики производительности ПК",
        "Подготовлена дистанционная диагностика ПК. Проверка начнётся после подтверждения инженером.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "file_lock_planned",
        1,
        "План проверки блокировки файла",
        "Проверяем, каким процессом или сеансом занят файл. Перед снятием блокировки инженер подтвердит безопасный способ.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "peripheral_setup_planned",
        1,
        "План подключения периферии",
        "Данные для подключения устройства собраны. Установка будет выполнена после проверки совместимости.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "peripheral_diagnostics_planned",
        1,
        "План диагностики периферии",
        "Уточняем подключение и симптом устройства, затем инженер проверит настройки на рабочей станции.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
    (
        "os_reinstallation_planned",
        1,
        "План согласования переустановки ОС",
        "Перед переустановкой необходимо подтвердить резервную копию и согласовать время недоступности ПК.",
        "resolution",
        27,
        "В работе",
        10,
        True,
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    for key, version, name, text, kind, target_status, status_name, expenses, requires_approval in TEMPLATES_DATA:
        if is_postgres:
            op.execute(
                sa.text(
                    \"\"\"
                    INSERT INTO response_templates (key, version, name, template_text, required_variables, is_active, created_by)
                    VALUES (:key, :version, :name, :text, '[]'::jsonb, true, 'system:migration:0013')
                    ON CONFLICT (key, version) DO NOTHING
                    \"\"\"
                ).bindparams(key=key, version=version, name=name, text=text)
            )
            op.execute(
                sa.text(
                    \"\"\"
                    INSERT INTO resolution_policies
                        (outcome_key, version, outcome_kind, template_id, target_status_id, status_name, expenses, risk_level, requires_approval, is_active, created_by)
                    SELECT :key, :version, :kind, template.id, :target_status, :status_name, :expenses, 0, :requires_approval, true, 'system:migration:0013'
                    FROM response_templates AS template
                    WHERE template.key = :key AND template.version = :version
                    ON CONFLICT (outcome_key, version) DO NOTHING
                    \"\"\"
                ).bindparams(
                    key=key,
                    version=version,
                    kind=kind,
                    target_status=target_status,
                    status_name=status_name,
                    expenses=expenses,
                    requires_approval=requires_approval,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DELETE FROM resolution_policies WHERE created_by = 'system:migration:0013'"
            )
        )
        op.execute(
            sa.text(
                "DELETE FROM response_templates WHERE created_by = 'system:migration:0013'"
            )
        )
