import asyncio
import calendar
import csv
import io
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.intraservice import _make_request
from app.services.service_catalog import ROOT_SERVICES, SERVICE_ID_TO_ROOT
from app.services.worker import get_redis_client

logger = logging.getLogger(__name__)

MONTH_NAMES = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

# Инвертированный маппинг корневых разделов к строке ID подсервисов
ROOT_TO_SIDS_STR: dict[str, str] = {}
for _sid, _r_num in SERVICE_ID_TO_ROOT.items():
    ROOT_TO_SIDS_STR.setdefault(_r_num, []).append(str(_sid))
ROOT_TO_SIDS_STR = {k: ",".join(v) for k, v in ROOT_TO_SIDS_STR.items()}

# Ключевые аналитические корни техподдержки
CORE_SERVICE_ROOTS = [
    ("03", "Принтеры и оргтехника"),
    ("02", "Настройка ПО и рабочих мест"),
    ("09", "ЭЦП и банк-клиенты"),
    ("01", "Учетные записи и доступ"),
    ("04", "Сеть и интернет"),
    ("05", "Directum и B2B"),
    ("06", "Вопросы по 1С"),
    ("10", "Телефония"),
]


def count_workdays(start_date: date, end_date: date) -> int:
    """Подсчет рабочих дней (пн-пт) между двумя датами включительно."""
    cur = start_date
    count = 0
    while cur <= end_date:
        if cur.weekday() < 5:  # 0..4 = Понедельник..Пятница
            count += 1
        cur += timedelta(days=1)
    return count


class ServiceShareMetric(BaseModel):
    key: str
    name: str
    count: int
    share_pct: float


class ExecutiveInsight(BaseModel):
    id: str
    title: str
    value: str
    description: str
    tone: Literal["positive", "neutral", "warning", "negative", "accent"] = "neutral"


class MonthLoadMetric(BaseModel):
    year: int
    month: int
    month_name: str
    period_label: str
    start_date_str: str
    end_date_str: str

    # Метрики Технической поддержки
    created: int = Field(description="Поступило в техподдержку")
    created_line1: int = Field(default=0, description="Поступило на 1-ю линию ТП")
    created_line2: int = Field(default=0, description="Поступило на 2-ю линию ТП")
    closed: int = Field(description="Обработано или закрыто техподдержкой в периоде")

    workdays: int = Field(description="Количество рабочих дней в периоде")
    daily_workday: float = Field(description="Средняя нагрузка ТП в рабочий день (поступило)")
    daily_closed_workday: float = Field(default=0.0, description="Среднее количество закрытых заявок ТП в рабочий день")
    is_current: bool = Field(default=False, description="Признак текущего неполного месяца")
    projected_created: int = Field(default=0, description="Прогноз поступления в ТП до конца месяца")
    balance: int = Field(description="Разница ТП (Поступило - Закрыто)")
    clear_rate: float = Field(description="Процент закрытия ТП (Закрыто / Поступило * 100)")
    mom_delta_pct: float | None = Field(default=None, description="Отклонение к предыдущему месяцу (%)")

    # Метрики качества и SLA
    overdue_reaction: int = Field(default=0, description="Количество заявок с просроченной реакцией")
    overdue_resolution: int = Field(default=0, description="Количество заявок с просроченным решением")
    sla_resolution_rate: float = Field(default=100.0, description="Процент соблюдения SLA решения (%)")

    # Распределение по сервисам внутри месяца
    service_breakdown: list[ServiceShareMetric] = Field(default_factory=list, description="Топ сервисов месяца")

    # Смежные направления
    created_1c: int = Field(default=0, description="Поступило в 1С (все линии)")
    created_directum: int = Field(default=0, description="Поступило в Directum")
    created_ats: int = Field(default=0, description="Поступило в АТС / Телефонию")
    total_tracked: int = Field(default=0, description="Всего учтенных обращений (ТП + 1С + Directum + АТС)")
    tp_share_pct: float = Field(default=0.0, description="Доля техподдержки от общего потока (%)")


class YearLoadReport(BaseModel):
    generated_at: str
    period_label: str
    scope: str = Field(default="all", description="all | line1 | line2")
    months: list[MonthLoadMetric]

    # Итоги Технической поддержки
    total_created: int = Field(description="Всего поступило в ТП")
    total_line1: int = Field(default=0, description="Суммарно на 1-ю линию ТП")
    total_line2: int = Field(default=0, description="Суммарно на 2-ю линию ТП")
    total_closed: int = Field(description="Всего обработано или закрыто в ТП")
    total_balance: int = Field(default=0, description="Суммарный прирост/сокращение остатка ТП")
    overall_clear_rate: float = Field(description="Общий процент закрытия ТП")
    avg_monthly_created: float
    avg_daily_workday: float
    avg_daily_closed_workday: float = Field(default=0.0)
    created_mom_delta_pct: float | None = Field(default=None, description="Трендовый рост к прошлому месяцу (%)")
    peak_month: str
    peak_created: int
    min_month: str
    min_created: int

    # Итоги SLA
    total_overdue_reaction: int = Field(default=0, description="Всего нарушений срока реакции")
    total_overdue_resolution: int = Field(default=0, description="Всего нарушений срока закрытия")
    overall_sla_resolution_rate: float = Field(default=100.0, description="Общий % соблюдения SLA решения")

    # Аналитические инсайты
    insights: list[ExecutiveInsight] = Field(default_factory=list, description="Ключевые аналитические выводы")

    # Общий срез по сервисам за весь период
    total_service_breakdown: list[ServiceShareMetric] = Field(default_factory=list, description="Распределение по сервисам за период")

    # Итоги смежных направлений
    total_1c: int = Field(default=0, description="Всего поступило в 1С")
    total_directum: int = Field(default=0, description="Всего поступило в Directum")
    total_ats: int = Field(default=0, description="Всего поступило в АТС")
    total_tracked: int = Field(default=0, description="Всего учтенных обращений")
    avg_tp_share_pct: float = Field(default=0.0, description="Средняя доля ТП от общего потока (%)")

    note: str | None = None
    from_cache: bool = False


async def get_tasks_count(auth_b64: str | None = None, **filters) -> int:
    """
    Возвращает точное число заявок по фильтру через Paginator.Count из IntraService API.
    """
    params = {"pagesize": 1, "archive": "true"}
    params.update(filters)
    data = await _make_request("task", method="GET", auth_b64=auth_b64, params=params)
    if isinstance(data, dict):
        paginator = data.get("Paginator")
        if isinstance(paginator, dict):
            return int(paginator.get("Count", 0))
        elif isinstance(paginator, list) and paginator:
            return int(paginator[0].get("Count", 0))
    return 0


async def fetch_month_metric(
    year: int,
    month: int,
    today: date,
    auth_b64: str | None = None,
    scope: str = "all",
    sem: asyncio.Semaphore | None = None,
    force_refresh: bool = False,
    full_history_refresh: bool = False,
) -> MonthLoadMetric:
    """
    Вычисляет метрики нагрузки, SLA и срез сервисов за указанный месяц с умным кэшированием в Redis.
    """
    redis = get_redis_client()
    is_current = (year == today.year and month == today.month)
    cache_key = f"reports:v2:load:month:{year}_{month:02d}:{scope}{':cur' if is_current else ''}"

    should_refresh = (is_current and force_refresh) or full_history_refresh
    if not should_refresh:
        try:
            cached_data = await redis.get(cache_key)
            if cached_data:
                parsed = json.loads(cached_data)
                return MonthLoadMetric(**parsed)
        except Exception as e:
            logger.warning("Ошибка чтения кэша месяца %s: %s", cache_key, e)

    async def _do_fetch() -> MonthLoadMetric:
        m_name = MONTH_NAMES[month]
        period_label = f"{m_name} {year}"
        last_day = calendar.monthrange(year, month)[1]
        start_d = date(year, month, 1)
        actual_end_day = today.day if is_current else last_day
        end_d = date(year, month, actual_end_day)

        start_date_str = f"{year}-{month:02d}-01"
        end_date_str = f"{year}-{month:02d}-{actual_end_day:02d}"

        workdays = max(1, count_workdays(start_d, end_d))
        month_workdays = count_workdays(start_d, date(year, month, last_day))

        start_str = f"{start_date_str} 00:00:00"
        end_str = f"{end_date_str} 23:59:59"

        if scope == "line1":
            target_groups = "15"
            fetch_l1, fetch_l2 = True, False
        elif scope == "line2":
            target_groups = "22"
            fetch_l1, fetch_l2 = False, True
        else:
            target_groups = "15,22"
            fetch_l1, fetch_l2 = True, True

        async def _zero() -> int:
            return 0

        # Базовые запросы ТП, SLA и смежных систем
        base_tasks = [
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds="15") if fetch_l1 else _zero(),
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds="22") if fetch_l2 else _zero(),
            get_tasks_count(auth_b64=auth_b64, ClosedMoreThan=start_str, ClosedLessThan=end_str, ExecutorGroupIds=target_groups),
            # SLA
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds=target_groups, ReactionOverdue="true"),
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds=target_groups, ResolutionOverdue="true"),
            # Смежные
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds="1,2,14"),
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds="6"),
            get_tasks_count(auth_b64=auth_b64, CreatedMoreThan=start_str, CreatedLessThan=end_str, ExecutorGroupIds="10"),
        ]

        # Запросы распределения по корневым сервисам ТП
        service_tasks = [
            get_tasks_count(
                auth_b64=auth_b64,
                CreatedMoreThan=start_str,
                CreatedLessThan=end_str,
                ExecutorGroupIds=target_groups,
                ServiceIds=ROOT_TO_SIDS_STR.get(r_num, ""),
            )
            for r_num, _ in CORE_SERVICE_ROOTS
        ]

        results = await asyncio.gather(*(base_tasks + service_tasks))

        c_line1 = results[0]
        c_line2 = results[1]
        closed_tp = results[2]
        overdue_react = results[3]
        overdue_res = results[4]
        c_1c = results[5]
        c_directum = results[6]
        c_ats = results[7]

        service_counts = results[8:]

        created_tp = c_line1 + c_line2
        total_tracked = created_tp + c_1c + c_directum + c_ats
        tp_share = round(created_tp / total_tracked * 100, 1) if total_tracked > 0 else 0.0

        daily_workday = round(created_tp / workdays, 1) if workdays > 0 else 0.0
        daily_closed_workday = round(closed_tp / workdays, 1) if workdays > 0 else 0.0
        projected_created = (
            round(created_tp / workdays * month_workdays)
            if is_current
            else created_tp
        )
        balance = created_tp - closed_tp
        clear_rate = round((closed_tp / created_tp * 100), 1) if created_tp > 0 else 0.0
        sla_resolution_rate = round(max(0.0, (1 - (overdue_res / created_tp)) * 100), 1) if created_tp > 0 else 100.0

        # Формирование разбивки по сервисам
        service_breakdown: list[ServiceShareMetric] = []
        accounted_service_tasks = 0
        for (r_num, r_name), cnt in zip(CORE_SERVICE_ROOTS, service_counts):
            if cnt > 0:
                share = round((cnt / created_tp * 100), 1) if created_tp > 0 else 0.0
                service_breakdown.append(ServiceShareMetric(key=r_num, name=r_name, count=cnt, share_pct=share))
                accounted_service_tasks += cnt

        # Остаток в "Прочие обращения"
        other_cnt = max(0, created_tp - accounted_service_tasks)
        if other_cnt > 0:
            other_share = round((other_cnt / created_tp * 100), 1) if created_tp > 0 else 0.0
            service_breakdown.append(ServiceShareMetric(key="other", name="Прочие обращения", count=other_cnt, share_pct=other_share))

        service_breakdown.sort(key=lambda x: x.count, reverse=True)

        metric = MonthLoadMetric(
            year=year,
            month=month,
            month_name=m_name,
            period_label=period_label,
            start_date_str=start_date_str,
            end_date_str=end_date_str,
            created=created_tp,
            created_line1=c_line1,
            created_line2=c_line2,
            closed=closed_tp,
            workdays=workdays,
            daily_workday=daily_workday,
            daily_closed_workday=daily_closed_workday,
            is_current=is_current,
            projected_created=projected_created,
            balance=balance,
            clear_rate=clear_rate,
            overdue_reaction=overdue_react,
            overdue_resolution=overdue_res,
            sla_resolution_rate=sla_resolution_rate,
            service_breakdown=service_breakdown,
            created_1c=c_1c,
            created_directum=c_directum,
            created_ats=c_ats,
            total_tracked=total_tracked,
            tp_share_pct=tp_share,
        )

        try:
            ttl = 300 if is_current else 2592000
            await redis.setex(cache_key, ttl, metric.model_dump_json())
        except Exception as e:
            logger.warning("Ошибка сохранения кэша месяца %s: %s", cache_key, e)

        return metric

    if sem:
        async with sem:
            return await _do_fetch()
    return await _do_fetch()


async def generate_load_report(
    months_count: int = 12,
    scope: str = "all",
    auth_b64: str | None = None,
    force_refresh: bool = False,
    full_history_refresh: bool = False,
    end_date: date | None = None,
) -> YearLoadReport:
    """
    Генерирует сводный отчет по нагрузке за указанное количество месяцев.
    """
    today = end_date or date.today()

    months_list: list[tuple[int, int]] = []
    cur_year, cur_month = today.year, today.month
    for _ in range(months_count):
        months_list.append((cur_year, cur_month))
        cur_month -= 1
        if cur_month == 0:
            cur_month = 12
            cur_year -= 1
    months_list.reverse()

    sem = asyncio.Semaphore(4)

    tasks = [
        fetch_month_metric(
            year=year,
            month=m,
            today=today,
            auth_b64=auth_b64,
            scope=scope,
            sem=sem,
            force_refresh=force_refresh,
            full_history_refresh=full_history_refresh,
        )
        for year, m in months_list
    ]

    metrics: list[MonthLoadMetric] = await asyncio.gather(*tasks)

    # Расчет MoM (Month over Month) дельты для каждого месяца
    for i in range(len(metrics)):
        if i > 0 and metrics[i - 1].created > 0:
            metrics[i].mom_delta_pct = round(
                ((metrics[i].created - metrics[i - 1].created) / metrics[i - 1].created * 100), 1
            )

    # Агрегаты ТП
    total_created = sum(m.created for m in metrics)
    total_line1 = sum(m.created_line1 for m in metrics)
    total_line2 = sum(m.created_line2 for m in metrics)
    total_closed = sum(m.closed for m in metrics)
    total_balance = total_created - total_closed
    overall_clear_rate = round(total_closed / total_created * 100, 1) if total_created > 0 else 0.0

    # Агрегаты SLA
    total_overdue_reaction = sum(m.overdue_reaction for m in metrics)
    total_overdue_resolution = sum(m.overdue_resolution for m in metrics)
    overall_sla_resolution_rate = (
        round(max(0.0, (1 - (total_overdue_resolution / total_created)) * 100), 1)
        if total_created > 0
        else 100.0
    )

    completed_months = [m for m in metrics if not m.is_current]
    if completed_months:
        avg_monthly_created = round(sum(m.created for m in completed_months) / len(completed_months), 1)
        avg_daily_workday = round(sum(m.daily_workday for m in completed_months) / len(completed_months), 1)
        avg_daily_closed_workday = round(sum(m.daily_closed_workday for m in completed_months) / len(completed_months), 1)
    else:
        avg_monthly_created = round(total_created / len(metrics), 1) if metrics else 0.0
        avg_daily_workday = round(sum(m.daily_workday for m in metrics) / len(metrics), 1) if metrics else 0.0
        avg_daily_closed_workday = round(sum(m.daily_closed_workday for m in metrics) / len(metrics), 1) if metrics else 0.0

    # Трендовый MoM последнего месяца
    created_mom_delta_pct = None
    if len(completed_months) >= 2 and completed_months[-2].created > 0:
        created_mom_delta_pct = round(
            ((completed_months[-1].created - completed_months[-2].created) / completed_months[-2].created * 100), 1
        )
    elif len(metrics) >= 2 and metrics[-2].created > 0:
        created_mom_delta_pct = round(
            ((metrics[-1].created - metrics[-2].created) / metrics[-2].created * 100), 1
        )

    # Агрегаты смежных направлений
    total_1c = sum(m.created_1c for m in metrics)
    total_directum = sum(m.created_directum for m in metrics)
    total_ats = sum(m.created_ats for m in metrics)
    total_tracked = sum(m.total_tracked for m in metrics)
    avg_tp_share_pct = round(total_created / total_tracked * 100, 1) if total_tracked > 0 else 0.0

    # Пик и минимум
    peak_m = max(metrics, key=lambda x: x.created) if metrics else None
    min_m = min([m for m in metrics if not m.is_current] or metrics, key=lambda x: x.created) if metrics else None

    first_label = metrics[0].period_label if metrics else ""
    last_label = metrics[-1].period_label if metrics else ""

    note = None
    if any(m.is_current for m in metrics):
        cur = [m for m in metrics if m.is_current][0]
        note = (
            f"Данные за {cur.period_label} приведены на {today.strftime('%d.%m.%Y')} "
            f"(неполный месяц, {cur.workdays} раб. дн.). Оценка входящего потока "
            f"к концу месяца при сохранении темпа: ~{cur.projected_created:,} заявок."
        )

    # Агрегация суммарного среза по сервисам за весь период
    service_totals: dict[str, dict[str, Any]] = {}
    for m in metrics:
        for s in m.service_breakdown:
            if s.key not in service_totals:
                service_totals[s.key] = {"name": s.name, "count": 0}
            service_totals[s.key]["count"] += s.count

    total_service_breakdown: list[ServiceShareMetric] = []
    for k, v in service_totals.items():
        cnt = v["count"]
        share = round((cnt / total_created * 100), 1) if total_created > 0 else 0.0
        total_service_breakdown.append(ServiceShareMetric(key=k, name=v["name"], count=cnt, share_pct=share))
    total_service_breakdown.sort(key=lambda x: x.count, reverse=True)

    # Аналитические инсайты (Executive Insights)
    insights: list[ExecutiveInsight] = []
    if peak_m:
        peak_diff_pct = (
            round(((peak_m.created - avg_monthly_created) / avg_monthly_created * 100), 1)
            if avg_monthly_created > 0
            else 0.0
        )
        insights.append(
            ExecutiveInsight(
                id="peak",
                title=f"Пик: {peak_m.period_label}",
                value=f"{peak_m.created:,} заявок".replace(",", " "),
                description=f"{peak_diff_pct:+.1f}% выше нормы ({avg_monthly_created:,.0f}/мес)",
                tone="accent",
            )
        )

    balance_tone: Literal["positive", "neutral", "warning", "negative", "accent"] = (
        "positive" if total_balance <= 0 else "warning"
    )
    balance_sign = f"+{total_balance}" if total_balance > 0 else str(total_balance)
    insights.append(
        ExecutiveInsight(
            id="balance",
            title="Баланс очереди",
            value=f"Clear Rate {overall_clear_rate}% ({balance_sign})",
            description="Остаток очереди сокращается" if total_balance <= 0 else "Входящий поток превышает выработку",
            tone=balance_tone,
        )
    )

    sla_tone: Literal["positive", "neutral", "warning", "negative", "accent"] = (
        "positive" if overall_sla_resolution_rate >= 95.0 else ("warning" if overall_sla_resolution_rate >= 90.0 else "negative")
    )
    insights.append(
        ExecutiveInsight(
            id="sla",
            title="SLA Регламент",
            value=f"{overall_sla_resolution_rate}% в срок",
            description=f"{total_overdue_resolution} просрочек решения за период",
            tone=sla_tone,
        )
    )

    if any(m.is_current for m in metrics):
        cur = [m for m in metrics if m.is_current][0]
        insights.append(
            ExecutiveInsight(
                id="forecast",
                title=f"Прогноз: {cur.month_name}",
                value=f"~{cur.projected_created:,} заявок".replace(",", " "),
                description=f"Темп ~{cur.daily_workday} заявок/день",
                tone="neutral",
            )
        )

    return YearLoadReport(
        generated_at=datetime.now().strftime("%d.%m.%Y %H:%M"),
        period_label=f"{first_label} — {last_label}",
        scope=scope,
        months=metrics,
        total_created=total_created,
        total_line1=total_line1,
        total_line2=total_line2,
        total_closed=total_closed,
        total_balance=total_balance,
        overall_clear_rate=overall_clear_rate,
        avg_monthly_created=avg_monthly_created,
        avg_daily_workday=avg_daily_workday,
        avg_daily_closed_workday=avg_daily_closed_workday,
        created_mom_delta_pct=created_mom_delta_pct,
        peak_month=peak_m.period_label if peak_m else "",
        peak_created=peak_m.created if peak_m else 0,
        min_month=min_m.period_label if min_m else "",
        min_created=min_m.created if min_m else 0,
        total_overdue_reaction=total_overdue_reaction,
        total_overdue_resolution=total_overdue_resolution,
        overall_sla_resolution_rate=overall_sla_resolution_rate,
        insights=insights,
        total_service_breakdown=total_service_breakdown,
        total_1c=total_1c,
        total_directum=total_directum,
        total_ats=total_ats,
        total_tracked=total_tracked,
        avg_tp_share_pct=avg_tp_share_pct,
        note=note,
    )


def export_load_report_csv(report: YearLoadReport) -> str:
    """
    Экспортирует отчет в CSV (разделитель ';', кодировка UTF-8 с BOM для Excel).
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")

    writer.writerow(["Отчёт по нагрузке службы поддержки"])
    writer.writerow([
        f"Период: {report.period_label}",
        f"Сформирован: {report.generated_at}",
        f"Контур: {report.scope}",
    ])
    writer.writerow([])

    # Шапка таблицы
    writer.writerow([
        "Период",
        "Поступило ТП",
        "MoM %",
        "1-я линия",
        "2-я линия",
        "Закрыто ТП",
        "Раб. дней",
        "Поступило в день",
        "Закрыто в день",
        "Баланс",
        "Clear Rate %",
        "Просрочено SLA (реакция)",
        "Просрочено SLA (решение)",
        "SLA решения %",
        "1С",
        "Directum",
        "АТС",
        "Всего учтено",
        "Доля ТП %",
        "Примечание",
    ])

    for m in report.months:
        status_note = "Текущий неполный" if m.is_current else ("Пик" if m.period_label == report.peak_month else "")
        mom_str = f"{m.mom_delta_pct:+.1f}%" if m.mom_delta_pct is not None else "-"
        writer.writerow([
            m.period_label,
            m.created,
            mom_str,
            m.created_line1,
            m.created_line2,
            m.closed,
            m.workdays,
            str(m.daily_workday).replace(".", ","),
            str(m.daily_closed_workday).replace(".", ","),
            m.balance,
            str(m.clear_rate).replace(".", ","),
            m.overdue_reaction,
            m.overdue_resolution,
            str(m.sla_resolution_rate).replace(".", ","),
            m.created_1c,
            m.created_directum,
            m.created_ats,
            m.total_tracked,
            str(m.tp_share_pct).replace(".", ","),
            status_note,
        ])

    writer.writerow([])
    writer.writerow([
        "ИТОГО ЗА ПЕРИОД",
        report.total_created,
        "-",
        report.total_line1,
        report.total_line2,
        report.total_closed,
        "-",
        str(report.avg_daily_workday).replace(".", ","),
        str(report.avg_daily_closed_workday).replace(".", ","),
        report.total_balance,
        str(report.overall_clear_rate).replace(".", ","),
        report.total_overdue_reaction,
        report.total_overdue_resolution,
        str(report.overall_sla_resolution_rate).replace(".", ","),
        report.total_1c,
        report.total_directum,
        report.total_ats,
        report.total_tracked,
        str(report.avg_tp_share_pct).replace(".", ","),
        "",
    ])

    return output.getvalue()
