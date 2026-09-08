"""
Базовый класс ActionHandler и оркестрация жизненного цикла Worker SDK.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ValidationError

from sdk.models import ActionResult, ExecutionPhase, HandlerContext, RiskClass

logger = logging.getLogger("execution_worker.sdk.base")

TInput = TypeVar("TInput", bound=BaseModel)


class ActionHandler(Generic[TInput], abc.ABC):
    """
    Абстрактный базовый класс обработчика действия (Action Handler).
    Каждое действие реализует строгий 7-фазный контракт.
    """

    id: str
    version: str = "1.0.0"
    capabilities: list[str] = []
    risk_class: RiskClass = RiskClass.NEVER_AUTO_RETRY
    input_model: type[TInput]

    # --- 7 обязательных фаз жизненного цикла ---

    async def validate(self, ctx: HandlerContext, params: TInput) -> tuple[bool, str]:
        """
        Фаза 1: Валидация входных параметров.
        Статическая проверка без внешних сетевых вызовов.
        """
        return True, "Параметры валидны"

    async def preflight(
        self, ctx: HandlerContext, params: TInput
    ) -> tuple[bool, str, dict[str, Any]]:
        """
        Фаза 2: Read-Only предпроверка доступности хоста, портов и служб.
        Выполняется ДО получения подтверждения оператора.
        Возвращает: (ok, message, evidence_dict).
        """
        return True, "Preflight пройден", {}

    async def prepare(self, ctx: HandlerContext, params: TInput) -> tuple[bool, str]:
        """
        Фаза 3: Подготовка окружения.
        Выполняется строго ПОСЛЕ approval (например, WMI bootstrap WinRM, staging драйверов).
        """
        return True, "Подготовка завершена"

    @abc.abstractmethod
    async def execute(self, ctx: HandlerContext, params: TInput) -> ActionResult:
        """
        Фаза 4: Непосредственное выполнение полезной нагрузки.
        Основной side-effect на целевой системе.
        """
        raise NotImplementedError

    async def verify(
        self, ctx: HandlerContext, params: TInput, result: ActionResult
    ) -> tuple[bool, str, bool, str | None]:
        """
        Фаза 5: Независимая верификация примененного состояния.
        Возвращает: (verified, message, is_failure, failure_code).
        """
        if not result.success:
            return False, result.message, True, result.failure_code
        return True, "Состояние успешно верифицировано", False, None

    async def reconcile(self, ctx: HandlerContext, params: TInput) -> tuple[bool, str]:
        """
        Фаза 6: Проверка состояния перед повтором при неопределенном исходе.
        """
        return False, "Reconcile не реализован для данного действия"

    async def cleanup(self, ctx: HandlerContext, params: TInput) -> None:
        """
        Фаза 7: Гарантированная зачистка временных файлов и откат настроек.
        Всегда вызывается в блоке finally, независимо от исхода.
        """
        pass

    # --- Оркестрация жизненного цикла ---

    async def run_pipeline(
        self,
        ctx: HandlerContext,
        raw_params: dict[str, Any],
        *,
        run_preflight: bool = True,
        run_prepare: bool = True,
    ) -> ActionResult:
        """
        Запускает полный конвейер жизненного цикла с проверкой cooperative cancellation
        и гарантированным вызовом фазы cleanup.
        """
        params: TInput | None = None
        try:
            # Десериализация и Pydantic-валидация
            try:
                params = self.input_model.model_validate(raw_params)
            except ValidationError as val_err:
                msg = f"Ошибка валидации схемы входных данных: {val_err}"
                ctx.log.append(msg)
                return ActionResult(
                    success=False,
                    message=msg,
                    error=str(val_err),
                    failure_kind="validation_error",
                    log=ctx.log,
                )

            # 1. Фаза VALIDATE
            self._check_cancellation(ctx, ExecutionPhase.VALIDATE)
            val_ok, val_msg = await self.validate(ctx, params)
            if not val_ok:
                ctx.log.append(f"Validation failed: {val_msg}")
                return ActionResult(
                    success=False,
                    message=val_msg,
                    failure_kind="validation_failed",
                    log=ctx.log,
                )

            # 2. Фаза PREFLIGHT (read-only)
            if run_preflight:
                self._check_cancellation(ctx, ExecutionPhase.PREFLIGHT)
                pf_ok, pf_msg, pf_evidence = await self.preflight(ctx, params)
                ctx.evidence.update(pf_evidence)
                if not pf_ok:
                    ctx.log.append(f"Preflight failed: {pf_msg}")
                    return ActionResult(
                        success=False,
                        message=pf_msg,
                        failure_kind="preflight_failed",
                        failure_code=pf_evidence.get("failure_code"),
                        payload={"evidence": ctx.evidence},
                        log=ctx.log,
                    )

            # 3. Фаза PREPARE (после approval)
            if run_prepare:
                self._check_cancellation(ctx, ExecutionPhase.PREPARE)
                prep_ok, prep_msg = await self.prepare(ctx, params)
                if not prep_ok:
                    ctx.log.append(f"Prepare failed: {prep_msg}")
                    return ActionResult(
                        success=False,
                        message=prep_msg,
                        failure_kind="prepare_failed",
                        failure_code="prepare_failed",
                        log=ctx.log,
                    )

            # 4. Фаза EXECUTE
            self._check_cancellation(ctx, ExecutionPhase.EXECUTE)
            exec_result = await self.execute(ctx, params)
            ctx.log.extend(exec_result.log)

            # 5. Фаза VERIFY
            self._check_cancellation(ctx, ExecutionPhase.VERIFY)
            ver_ok, ver_msg, is_failure, failure_code = await self.verify(ctx, params, exec_result)
            if not ver_ok:
                ctx.log.append(f"Verification check: {ver_msg}")
                return ActionResult(
                    success=False,
                    message=ver_msg,
                    error=exec_result.error,
                    failure_kind="verification_failed",
                    failure_code=failure_code or exec_result.failure_code,
                    verified_failure=is_failure,
                    payload=exec_result.payload,
                    log=ctx.log,
                )

            return ActionResult(
                success=True,
                message=ver_msg,
                payload=exec_result.payload,
                log=ctx.log,
            )

        except asyncio.CancelledError:
            ctx.log.append("Execution pipeline cancelled via cancellation_token or task cancel")
            return ActionResult(
                success=False,
                message="Операция была отменена",
                failure_kind="cancelled",
                log=ctx.log,
            )
        except Exception as exc:
            logger.exception("Непредвиденная ошибка в конвейере %s: %s", self.id, exc)
            ctx.log.append(f"Unhandled exception: {exc}")
            return ActionResult(
                success=False,
                message=f"Системный сбой при исполнении: {exc}",
                error=str(exc),
                failure_kind="unhandled_exception",
                log=ctx.log,
            )
        finally:
            # 7. Фаза CLEANUP (гарантированное исполнение)
            if params is not None:
                try:
                    await self.cleanup(ctx, params)
                except Exception as cl_exc:
                    logger.warning("Ошибка в фазе cleanup для %s: %s", self.id, cl_exc)
                    ctx.log.append(f"Cleanup error: {cl_exc}")

    @staticmethod
    def _check_cancellation(ctx: HandlerContext, phase: ExecutionPhase) -> None:
        """Проверяет токен отмены перед входом в следующую фазу."""
        if ctx.cancellation_token.is_set():
            raise asyncio.CancelledError(f"Cancelled before phase {phase.value}")
