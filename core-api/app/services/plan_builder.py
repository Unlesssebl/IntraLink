"""Deterministic ExecutionPlan builder and progress projection for scenario-driven execution."""

from __future__ import annotations

import datetime as dt
from typing import Any

from shared.domain import (
    ExecutionPlan,
    FactBag,
    FactState,
    PlanStep,
    StepKind,
    StepStatus,
    get_scenario_display_name,
)

DIAGNOSTIC_TTL_SECONDS = 900  # 15 minutes fresh window for network/host diagnostics


def _is_diagnostic_fresh(diag: dict[str, Any] | None, expected_target: str | None = None) -> bool:
    if not diag:
        return False
    if expected_target:
        diag_host = str(diag.get("host") or diag.get("target") or "").strip().upper()
        if diag_host and diag_host != expected_target.strip().upper():
            return False
    collected_at_raw = (
        diag.get("timestamp")
        or diag.get("collected_at")
        or diag.get("observed_at")
        or diag.get("checked_at")
    )
    if not collected_at_raw:
        # If diagnostics explicitly present in current run payload without timestamp, consider fresh for this run
        return True
    try:
        if isinstance(collected_at_raw, (int, float)):
            collected_dt = dt.datetime.fromtimestamp(collected_at_raw, dt.timezone.utc)
        elif isinstance(collected_at_raw, dt.datetime):
            collected_dt = (
                collected_at_raw if collected_at_raw.tzinfo else collected_at_raw.replace(tzinfo=dt.timezone.utc)
            )
        else:
            collected_dt = dt.datetime.fromisoformat(str(collected_at_raw).replace("Z", "+00:00"))
        now = dt.datetime.now(dt.timezone.utc)
        return (now - collected_dt).total_seconds() <= DIAGNOSTIC_TTL_SECONDS
    except Exception:
        return True


class PlanBuilder:
    """Builds structured ExecutionPlan v2 and projects runtime progress."""

    @staticmethod
    def build(
        *,
        scenario_key: str,
        scenario_version: int = 1,
        facts: FactBag | None = None,
        context: Any = None,
        diagnostics: dict[str, Any] | None = None,
        commands: list[Any] | None = None,
        latest_command: Any = None,
        events: list[Any] | None = None,
        manual_actions: list[dict[str, Any]] | None = None,
    ) -> ExecutionPlan:
        facts_bag = facts or FactBag()
        diag = diagnostics or {}
        cmds = list(commands or [])
        if latest_command and latest_command not in cmds:
            cmds.append(latest_command)
        evts = list(events or [])
        manuals = {
            m.get("step_id"): m
            for m in (manual_actions or [])
            if isinstance(m, dict) and m.get("step_id")
        }

        builder_method = getattr(PlanBuilder, f"_build_{scenario_key}", PlanBuilder._build_default)
        steps = builder_method(
            scenario_key=scenario_key,
            scenario_version=scenario_version,
            facts=facts_bag,
            diag=diag,
            commands=cmds,
            events=evts,
            manuals=manuals,
        )

        phase = PlanBuilder._determine_phase(steps, facts_bag, cmds, evts)
        next_action = PlanBuilder._next_action_description(steps, phase)
        title = get_scenario_display_name(scenario_key, version=scenario_version)

        return ExecutionPlan(
            schema_version=2,
            scenario_key=scenario_key,
            scenario_version=scenario_version,
            fact_revision=facts_bag.revision,
            title=title,
            phase=phase,
            next_action_description=next_action,
            steps=steps,
        )

    @staticmethod
    def _determine_phase(
        steps: list[PlanStep],
        facts: FactBag,
        commands: list[Any],
        events: list[Any],
    ) -> str:
        if any(step.status == StepStatus.FAILED for step in steps):
            return "blocked"

        req_steps = [s for s in steps if s.required_for_resolution]
        if req_steps and all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in req_steps):
            return "completed"

        running_commands = [
            c for c in commands
            if getattr(c, "status", None) in ("running", "queued", "claimed")
            or (isinstance(c, dict) and c.get("status") in ("running", "queued", "claimed"))
        ]
        if running_commands or any(step.status == StepStatus.RUNNING for step in steps):
            return "running"

        dispatched_commands = [
            c for c in commands
            if getattr(c, "status", None) in ("dispatched", "awaiting_approval")
            or (isinstance(c, dict) and c.get("status") in ("dispatched", "awaiting_approval"))
        ]
        if dispatched_commands:
            return "dispatched"

        action_steps = [s for s in steps if s.kind in (StepKind.ACTION, StepKind.MANUAL) and s.required_for_resolution]
        verify_steps = [s for s in steps if s.kind == StepKind.VERIFY and s.required_for_resolution]
        if action_steps and all(s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in action_steps):
            if any(s.status not in (StepStatus.COMPLETED, StepStatus.SKIPPED) for s in verify_steps):
                return "verifying"

        missing_or_invalid = [
            f for f in facts.facts.values()
            if f.state in (FactState.MISSING, FactState.INVALID, FactState.CONFLICTING, FactState.AMBIGUOUS)
        ]
        if missing_or_invalid or any(step.status == StepStatus.WAITING_INPUT for step in steps):
            return "collecting"

        return "ready"

    @staticmethod
    def _next_action_description(steps: list[PlanStep], phase: str) -> str:
        if phase == "completed":
            return "Все запланированные шаги выполнены. Решение готово к финализации."
        if phase == "blocked":
            failed = next((s for s in steps if s.status == StepStatus.FAILED), None)
            return f"Выполнение остановлено: шаг '{failed.title if failed else '?'}' завершился с ошибкой."
        if phase == "running":
            running = next((s for s in steps if s.status == StepStatus.RUNNING), None)
            return f"Выполняется: {running.title if running else 'автоматическая операция'}..."
        if phase == "dispatched":
            return "Команда отправлена на исполнение, ожидается подтверждение или запуск воркера."
        if phase == "verifying":
            return "Основное действие выполнено. Ожидается проверка результата пользователем или инженером."
        if phase == "collecting":
            return "Сбор обязательных сведений и уточнение недостающих параметров."
        return "План проверок и действий сформирован. Ожидает запуска инженером."

    # --- Scenario-specific plan builders ---

    @staticmethod
    def _build_install_printer(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        printer_name = str(facts.valid_value("printer_name") or "").strip()
        printer_address = str(facts.valid_value("printer_address") or "").strip()

        has_params = bool(pc_name and (printer_name or printer_address))
        step1 = PlanStep(
            id="check_params",
            title="Проверка связности параметров установки принтера",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED if has_params else StepStatus.WAITING_INPUT,
            target_ref=pc_name or None,
            result_summary=(
                f"ПК: {pc_name}, Принтер: {printer_name or printer_address}"
                if has_params
                else "Не все параметры указаны"
            ),
            evidence_refs=[f"fact:pc_name:{pc_name}"] if pc_name else [],
        )

        is_fresh = _is_diagnostic_fresh(diag, pc_name)
        diag_ok = is_fresh and (diag.get("ping") is True or diag.get("online") is True)
        diag_failed = is_fresh and (diag.get("ping") is False or diag.get("online") is False)
        step2_status = (
            StepStatus.COMPLETED if diag_ok
            else (StepStatus.FAILED if diag_failed
                  else (StepStatus.READY if has_params else StepStatus.NOT_STARTED))
        )
        step2 = PlanStep(
            id="preflight_diagnostics",
            title="Preflight-диагностика доступности хоста в сети",
            kind=StepKind.CHECK,
            capability_id="diagnose_host",
            executor="backend",
            status=step2_status,
            target_ref=pc_name or None,
            requires=["check_params"],
            evidence_refs=["diagnostic:host_telemetry:online"] if diag_ok else [],
            result_summary="Хост доступен по сети" if diag_ok else ("Хост недоступен" if diag_failed else None),
        )

        approval_cmd = next(
            (c for c in commands if getattr(c, "action", None) == "install_printer" or (isinstance(c, dict) and c.get("action") == "install_printer")),
            None
        )
        step3_status = (
            StepStatus.COMPLETED if approval_cmd
            else (StepStatus.READY if step2_status == StepStatus.COMPLETED else StepStatus.NOT_STARTED)
        )
        step3 = PlanStep(
            id="engineer_approval",
            title="Согласование параметров установки инженером 1-й линии",
            kind=StepKind.APPROVE,
            executor="engineer",
            status=step3_status,
            target_ref=pc_name or None,
            requires=["preflight_diagnostics"],
            evidence_refs=[f"command:{getattr(approval_cmd, 'id', None)}:approved"] if approval_cmd else [],
            result_summary="Параметры подтверждены" if approval_cmd else None,
        )

        cmd_status = getattr(approval_cmd, "status", None) if approval_cmd else (approval_cmd.get("status") if isinstance(approval_cmd, dict) else None)
        cmd_id = getattr(approval_cmd, "id", None) if approval_cmd else (approval_cmd.get("id") if isinstance(approval_cmd, dict) else None)
        step4_status = StepStatus.NOT_STARTED
        step4_evidence: list[str] = []
        if cmd_status == "succeeded":
            step4_status = StepStatus.COMPLETED
            step4_evidence = [f"worker:command:{cmd_id}:succeeded"]
        elif cmd_status in ("running", "queued", "claimed"):
            step4_status = StepStatus.RUNNING
        elif cmd_status in ("failed", "rejected"):
            step4_status = StepStatus.FAILED
        elif step3_status == StepStatus.COMPLETED:
            step4_status = StepStatus.READY

        step4 = PlanStep(
            id="install_printer",
            title="Установка драйвера и подключение принтера на рабочей станции",
            kind=StepKind.ACTION,
            capability_id="install_printer",
            executor="windows",
            required_for_resolution=True,
            status=step4_status,
            target_ref=pc_name or None,
            requires=["engineer_approval"],
            evidence_refs=step4_evidence,
            result_summary="Принтер успешно установлен воркером" if step4_status == StepStatus.COMPLETED else None,
        )

        manual5 = manuals.get("verify_test_page")
        step5_status = StepStatus.NOT_STARTED
        step5_evidence: list[str] = []
        if manual5 and manual5.get("completed"):
            step5_status = StepStatus.COMPLETED
            step5_evidence = manual5.get("evidence_refs") or [f"manual:verify_test_page:{manual5.get('actor', 'operator')}"]
        elif cmd_status == "succeeded":
            step5_status = StepStatus.COMPLETED
            step5_evidence = [f"worker:command:{cmd_id}:verified_success"]
        elif step4_status == StepStatus.COMPLETED:
            step5_status = StepStatus.READY

        step5 = PlanStep(
            id="verify_test_page",
            title="Печать пробной страницы и подтверждение работоспособности",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=step5_status,
            target_ref=pc_name or None,
            requires=["install_printer"],
            evidence_refs=step5_evidence,
            completed_by=manual5.get("actor") if manual5 else None,
            completed_at=manual5.get("observed_at") if manual5 else None,
            result_summary="Пробная печать подтверждена" if step5_status == StepStatus.COMPLETED else None,
        )

        return [step1, step2, step3, step4, step5]

    @staticmethod
    def _build_printer_hardware_service(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        printer_address = str(facts.valid_value("printer_address") or facts.valid_value("printer_name") or "").strip()
        defect_type = str(facts.valid_value("defect_type") or "hardware_service").strip()

        step1 = PlanStep(
            id="identify_defect",
            title="Идентификация устройства и аппаратного дефекта оргтехники",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED if printer_address else StepStatus.WAITING_INPUT,
            target_ref=printer_address or None,
            evidence_refs=[f"fact:printer_address:{printer_address}"] if printer_address else [],
            result_summary=f"Дефект: {defect_type}" if printer_address else "Устройство не указано",
        )

        is_fresh = _is_diagnostic_fresh(diag, printer_address)
        step2_status = StepStatus.COMPLETED if (is_fresh and diag.get("ping")) else (
            StepStatus.READY if printer_address else StepStatus.NOT_STARTED
        )
        step2 = PlanStep(
            id="remote_check",
            title="Исключение удалённых причин и проверка доступности сетевого интерфейса",
            kind=StepKind.CHECK,
            capability_id="diagnose_host",
            executor="backend",
            status=step2_status,
            target_ref=printer_address or None,
            requires=["identify_defect"],
            evidence_refs=["diagnostic:printer:ping"] if step2_status == StepStatus.COMPLETED else [],
        )

        manual3 = manuals.get("assign_hardware_service")
        step3_status = StepStatus.COMPLETED if (manual3 and manual3.get("completed")) else StepStatus.READY
        step3 = PlanStep(
            id="assign_hardware_service",
            title="Назначение выезда инженера / передача в сервисную службу",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=step3_status,
            target_ref=printer_address or None,
            requires=["remote_check"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", [f"manual:service_assigned:{manual3.get('actor')}"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("verify_hardware_service")
        step4_status = StepStatus.COMPLETED if (manual4 and manual4.get("completed")) else StepStatus.NOT_STARTED
        step4 = PlanStep(
            id="verify_hardware_service",
            title="Подтверждение устранения дефекта и исправности МФУ",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=step4_status,
            target_ref=printer_address or None,
            requires=["assign_hardware_service"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", [f"manual:verified:{manual4.get('actor')}"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_printer_print_failure(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        printer_name = str(facts.valid_value("printer_name") or "").strip()

        step1 = PlanStep(
            id="check_connectivity",
            title="Проверка доступности ПК и принтера в локальной сети",
            kind=StepKind.CHECK,
            capability_id="diagnose_host",
            executor="backend",
            status=StepStatus.COMPLETED if _is_diagnostic_fresh(diag, pc_name) and diag.get("ping") else (
                StepStatus.READY if pc_name else StepStatus.WAITING_INPUT
            ),
            target_ref=pc_name or None,
            evidence_refs=["diagnostic:host:ping"] if _is_diagnostic_fresh(diag, pc_name) and diag.get("ping") else [],
        )

        step2 = PlanStep(
            id="check_spooler",
            title="Диагностика службы диспетчера печати (Spooler) и очереди заданий",
            kind=StepKind.CHECK,
            executor="engineer",
            status=StepStatus.READY if pc_name else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["check_connectivity"],
        )

        manual3 = manuals.get("restart_spooler")
        step3 = PlanStep(
            id="restart_spooler",
            title="Очистка очереди печати и перезапуск службы Spooler",
            kind=StepKind.ACTION,
            executor="windows",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_spooler"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", ["manual:spooler_restarted"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("verify_print")
        step4 = PlanStep(
            id="verify_print",
            title="Контрольная печать тестовой страницы",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["restart_spooler"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", [f"manual:print_verified:{manual4.get('actor')}"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_printer_scan_failure(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        conn_type = str(facts.valid_value("scan_connection_type") or facts.valid_value("connection_type") or "").strip().lower()
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        printer_address = str(facts.valid_value("printer_address") or "").strip()
        scan_path = str(facts.valid_value("scan_path") or "").strip()

        is_usb = conn_type == "usb"
        is_network = conn_type in ("network", "smb", "ftp") or (not is_usb and bool(printer_address or scan_path))

        if is_network:
            step1 = PlanStep(
                id="check_network_and_smb",
                title="Проверка доступности МФУ и сетевого порта SMB:445",
                kind=StepKind.CHECK,
                capability_id="diagnose_host",
                executor="backend",
                status=StepStatus.COMPLETED if _is_diagnostic_fresh(diag, printer_address or pc_name) and (diag.get("smb_445") or diag.get("ping")) else StepStatus.READY,
                target_ref=printer_address or pc_name or None,
                evidence_refs=["diagnostic:network_smb:ok"] if _is_diagnostic_fresh(diag, printer_address or pc_name) and diag.get("smb_445") else [],
            )
            step2 = PlanStep(
                id="check_scan_folder",
                title="Проверка каталога назначения и прав учетной записи сканирования",
                kind=StepKind.CHECK,
                executor="backend",
                status=StepStatus.COMPLETED if scan_path else StepStatus.WAITING_INPUT,
                target_ref=scan_path or None,
                requires=["check_network_and_smb"],
                evidence_refs=[f"fact:scan_path:{scan_path}"] if scan_path else [],
            )
            manual3 = manuals.get("configure_scan_folder")
            step3 = PlanStep(
                id="configure_scan_folder",
                title="Настройка сетевой папки сканирования или прав доступа к ресурсу",
                kind=StepKind.ACTION,
                executor="engineer",
                required_for_resolution=True,
                status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
                target_ref=scan_path or None,
                requires=["check_scan_folder"],
                completed_by=manual3.get("actor") if manual3 else None,
                completed_at=manual3.get("observed_at") if manual3 else None,
                evidence_refs=manual3.get("evidence_refs", ["manual:scan_folder_configured"]) if manual3 and manual3.get("completed") else [],
            )
            manual4 = manuals.get("verify_scan")
            step4 = PlanStep(
                id="verify_scan",
                title="Тестовое сетевое сканирование документа с МФУ в целевую папку",
                kind=StepKind.VERIFY,
                executor="engineer",
                required_for_resolution=True,
                status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
                target_ref=printer_address or scan_path or None,
                requires=["configure_scan_folder"],
                completed_by=manual4.get("actor") if manual4 else None,
                completed_at=manual4.get("observed_at") if manual4 else None,
                evidence_refs=manual4.get("evidence_refs", ["manual:scan_verified"]) if manual4 and manual4.get("completed") else [],
            )
            return [step1, step2, step3, step4]
        else:
            step1 = PlanStep(
                id="check_usb_connection",
                title="Проверка физического USB-подключения и статуса устройства",
                kind=StepKind.MANUAL,
                executor="engineer",
                status=StepStatus.READY,
                target_ref=pc_name or None,
            )
            step2 = PlanStep(
                id="check_wia_twain",
                title="Проверка службы WIA и компонентов сканирования Windows (ручной осмотр)",
                kind=StepKind.MANUAL,
                executor="engineer",
                status=StepStatus.READY,
                target_ref=pc_name or None,
                requires=["check_usb_connection"],
            )
            manual3 = manuals.get("reinstall_scan_driver")
            step3 = PlanStep(
                id="reinstall_scan_driver",
                title="Настройка или переустановка драйвера сканирования",
                kind=StepKind.MANUAL,
                executor="engineer",
                required_for_resolution=True,
                status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
                target_ref=pc_name or None,
                requires=["check_wia_twain"],
                completed_by=manual3.get("actor") if manual3 else None,
                completed_at=manual3.get("observed_at") if manual3 else None,
                evidence_refs=manual3.get("evidence_refs", ["manual:scan_driver_installed"]) if manual3 and manual3.get("completed") else [],
            )
            manual4 = manuals.get("verify_scan")
            step4 = PlanStep(
                id="verify_scan",
                title="Контрольное сканирование через стандартное приложение Windows",
                kind=StepKind.VERIFY,
                executor="engineer",
                required_for_resolution=True,
                status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
                target_ref=pc_name or None,
                requires=["reinstall_scan_driver"],
                completed_by=manual4.get("actor") if manual4 else None,
                completed_at=manual4.get("observed_at") if manual4 else None,
                evidence_refs=manual4.get("evidence_refs", ["manual:scan_verified"]) if manual4 and manual4.get("completed") else [],
            )
            return [step1, step2, step3, step4]

    @staticmethod
    def _build_pc_performance(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        is_fresh = _is_diagnostic_fresh(diag, pc_name)

        step1 = PlanStep(
            id="check_host_online",
            title="Проверка сетевой доступности рабочей станции",
            kind=StepKind.CHECK,
            capability_id="diagnose_host",
            executor="backend",
            status=StepStatus.COMPLETED if is_fresh and diag.get("ping") else (
                StepStatus.READY if pc_name else StepStatus.WAITING_INPUT
            ),
            target_ref=pc_name or None,
            evidence_refs=["diagnostic:host:ping"] if is_fresh and diag.get("ping") else [],
        )

        has_telemetry = is_fresh and bool(diag.get("disk") or diag.get("specs") or diag.get("free_disk_gb"))
        step2 = PlanStep(
            id="collect_telemetry",
            title="Сбор доступной телеметрии накопителей и аппаратных характеристик",
            kind=StepKind.CHECK,
            capability_id="diagnose_host",
            executor="backend",
            status=StepStatus.COMPLETED if has_telemetry else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_host_online"],
            evidence_refs=["diagnostic:telemetry:disk_specs"] if has_telemetry else [],
        )

        manual3 = manuals.get("analyze_workload")
        step3 = PlanStep(
            id="analyze_workload",
            title="Анализ фоновой нагрузки, автозагрузки и системных служб инженером",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["collect_telemetry"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", ["manual:workload_analyzed"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("verify_performance")
        step4 = PlanStep(
            id="verify_performance",
            title="Контрольная проверка быстродействия и стабильности работы ПК",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["analyze_workload"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", ["manual:performance_verified"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_file_lock(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        file_path = str(facts.valid_value("file_path") or "").strip()

        step1 = PlanStep(
            id="check_file_path",
            title="Проверка сетевого пути к файлу и доступности ресурса SMB",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED if file_path else StepStatus.WAITING_INPUT,
            target_ref=file_path or None,
            evidence_refs=[f"fact:file_path:{file_path}"] if file_path else [],
        )

        manual2 = manuals.get("find_locking_session")
        step2 = PlanStep(
            id="find_locking_session",
            title="Определение сеанса пользователя или процесса, удерживающего блокировку",
            kind=StepKind.CHECK,
            executor="backend",
            status=StepStatus.COMPLETED if manual2 and manual2.get("completed") else StepStatus.READY,
            target_ref=file_path or None,
            requires=["check_file_path"],
            completed_by=manual2.get("actor") if manual2 else None,
            completed_at=manual2.get("observed_at") if manual2 else None,
            evidence_refs=manual2.get("evidence_refs", ["diagnostic:file_lock:identified"]) if manual2 and manual2.get("completed") else [],
        )

        manual3 = manuals.get("release_file_lock")
        step3 = PlanStep(
            id="release_file_lock",
            title="Согласованное снятие блокировки файла или закрытие открытого сеанса",
            kind=StepKind.ACTION,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
            target_ref=file_path or None,
            requires=["find_locking_session"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", ["manual:file_lock_released"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("verify_file_access")
        step4 = PlanStep(
            id="verify_file_access",
            title="Проверка успешного открытия файла заявителем",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=file_path or None,
            requires=["release_file_lock"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", ["manual:file_access_verified"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_peripheral_diagnostics(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        device_type = str(facts.valid_value("device_type") or "").strip()

        step1 = PlanStep(
            id="check_physical_connection",
            title="Проверка физического подключения кабеля, разъемов и питания устройства",
            kind=StepKind.MANUAL,
            executor="engineer",
            status=StepStatus.READY,
            target_ref=pc_name or None,
        )

        step2 = PlanStep(
            id="check_device_manager",
            title="Проверка обнаружения устройства и ошибок в Диспетчере устройств Windows",
            kind=StepKind.MANUAL,
            executor="engineer",
            status=StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_physical_connection"],
        )

        manual3 = manuals.get("update_device_driver")
        step3 = PlanStep(
            id="update_device_driver",
            title="Переустановка или обновление драйвера периферийного оборудования",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_device_manager"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", ["manual:driver_updated"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("verify_device_operation")
        step4 = PlanStep(
            id="verify_device_operation",
            title="Функциональная проверка работоспособности устройства",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["update_device_driver"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", ["manual:device_operation_verified"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_peripheral_setup(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        device_type = str(facts.valid_value("device_type") or "").strip()

        step1 = PlanStep(
            id="check_compatibility",
            title="Проверка совместимости оборудования и разъемов подключения",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED if device_type else StepStatus.READY,
            target_ref=pc_name or None,
            evidence_refs=[f"fact:device_type:{device_type}"] if device_type else [],
        )

        manual2 = manuals.get("install_device_driver")
        step2 = PlanStep(
            id="install_device_driver",
            title="Подключение устройства и установка необходимых драйверов",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual2 and manual2.get("completed") else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_compatibility"],
            completed_by=manual2.get("actor") if manual2 else None,
            completed_at=manual2.get("observed_at") if manual2 else None,
            evidence_refs=manual2.get("evidence_refs", ["manual:peripheral_installed"]) if manual2 and manual2.get("completed") else [],
        )

        step3 = PlanStep(
            id="configure_device_settings",
            title="Настройка параметров устройства по умолчанию в Windows",
            kind=StepKind.MANUAL,
            executor="engineer",
            status=StepStatus.READY,
            target_ref=pc_name or None,
            requires=["install_device_driver"],
        )

        manual4 = manuals.get("verify_peripheral_setup")
        step4 = PlanStep(
            id="verify_peripheral_setup",
            title="Проверка корректной работы и краткий инструктаж пользователя",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["configure_device_settings"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", ["manual:peripheral_verified"]) if manual4 and manual4.get("completed") else [],
        )
        return [step1, step2, step3, step4]

    @staticmethod
    def _build_os_reinstallation(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        pc_name = str(facts.valid_value("pc_name") or "").strip()
        backup_confirmed = bool(facts.valid_value("backup_confirmed") or facts.valid_value("user_backup_confirmed"))

        step1 = PlanStep(
            id="verify_backup",
            title="Подтверждение сохранения важных файлов и готовности резервной копии",
            kind=StepKind.CHECK,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if backup_confirmed else StepStatus.WAITING_INPUT,
            target_ref=pc_name or None,
            evidence_refs=["fact:backup_confirmed:true"] if backup_confirmed else [],
            result_summary="Резервная копия подтверждена" if backup_confirmed else "Требуется согласование бэкапа",
        )

        step2 = PlanStep(
            id="check_hardware_health",
            title="Диагностика накопителя и аппаратных компонентов ПК",
            kind=StepKind.CHECK,
            executor="engineer",
            status=StepStatus.READY if pc_name else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["verify_backup"],
        )

        manual3 = manuals.get("reinstall_os")
        step3 = PlanStep(
            id="reinstall_os",
            title="Согласование окна недоступности и чистая установка ОС Windows",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual3 and manual3.get("completed") else StepStatus.READY,
            target_ref=pc_name or None,
            requires=["check_hardware_health"],
            completed_by=manual3.get("actor") if manual3 else None,
            completed_at=manual3.get("observed_at") if manual3 else None,
            evidence_refs=manual3.get("evidence_refs", ["manual:os_reinstalled"]) if manual3 and manual3.get("completed") else [],
        )

        manual4 = manuals.get("restore_data_and_apps")
        step4 = PlanStep(
            id="restore_data_and_apps",
            title="Установка стандартного корпоративного ПО и перенос профиля",
            kind=StepKind.MANUAL,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual4 and manual4.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["reinstall_os"],
            completed_by=manual4.get("actor") if manual4 else None,
            completed_at=manual4.get("observed_at") if manual4 else None,
            evidence_refs=manual4.get("evidence_refs", ["manual:profile_restored"]) if manual4 and manual4.get("completed") else [],
        )

        manual5 = manuals.get("verify_os_readiness")
        step5 = PlanStep(
            id="verify_os_readiness",
            title="Финальная проверка готовности рабочего места пользователем",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=StepStatus.COMPLETED if manual5 and manual5.get("completed") else StepStatus.NOT_STARTED,
            target_ref=pc_name or None,
            requires=["restore_data_and_apps"],
            completed_by=manual5.get("actor") if manual5 else None,
            completed_at=manual5.get("observed_at") if manual5 else None,
            evidence_refs=manual5.get("evidence_refs", ["manual:os_readiness_verified"]) if manual5 and manual5.get("completed") else [],
        )
        return [step1, step2, step3, step4, step5]

    @staticmethod
    def _build_default(
        *, scenario_key: str, scenario_version: int, facts: FactBag, diag: dict[str, Any],
        commands: list[Any], events: list[Any], manuals: dict[str, Any],
    ) -> list[PlanStep]:
        action_cmd = next(
            (c for c in commands if getattr(c, "action", None) or (isinstance(c, dict) and c.get("action"))),
            None
        )
        cmd_status = getattr(action_cmd, "status", None) if action_cmd else (action_cmd.get("status") if isinstance(action_cmd, dict) else None)
        cmd_id = getattr(action_cmd, "id", None) if action_cmd else (action_cmd.get("id") if isinstance(action_cmd, dict) else None)

        step1 = PlanStep(
            id="check_requirements",
            title="Проверка обязательных параметров и классификация обращения",
            kind=StepKind.CHECK,
            status=StepStatus.COMPLETED,
            evidence_refs=["rule:classifier:matched"],
        )

        step2_status = (
            StepStatus.COMPLETED if cmd_status == "succeeded"
            else (StepStatus.RUNNING if cmd_status in ("running", "queued", "claimed")
                  else (StepStatus.FAILED if cmd_status in ("failed", "rejected")
                        else StepStatus.READY))
        )
        step2 = PlanStep(
            id="execute_decision",
            title=f"Исполнение решения по регламенту {scenario_key}",
            kind=StepKind.ACTION,
            required_for_resolution=True,
            status=step2_status,
            requires=["check_requirements"],
            evidence_refs=[f"worker:command:{cmd_id}:succeeded"] if cmd_status == "succeeded" else [],
        )

        manual_verify = manuals.get("verify_resolution")
        step3_status = (
            StepStatus.COMPLETED if manual_verify and manual_verify.get("completed")
            else (StepStatus.READY if step2_status == StepStatus.COMPLETED else StepStatus.NOT_STARTED)
        )
        step3 = PlanStep(
            id="verify_resolution",
            title="Подтверждение решения и готовность к закрытию",
            kind=StepKind.VERIFY,
            executor="engineer",
            required_for_resolution=True,
            status=step3_status,
            requires=["execute_decision"],
            completed_by=manual_verify.get("actor") if manual_verify else None,
            completed_at=manual_verify.get("observed_at") if manual_verify else None,
            evidence_refs=manual_verify.get("evidence_refs", ["manual:resolution_verified"]) if manual_verify and manual_verify.get("completed") else [],
        )
        return [step1, step2, step3]
