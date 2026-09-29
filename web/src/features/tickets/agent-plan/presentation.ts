import type { TicketAutomation } from "@/features/autopilot/types";

export type PreflightItem = TicketAutomation["preflight"][number];

export const CASE_NAMES: Record<string, string> = {
  printer_connection_request: "Подключение принтера",
  printing_incident: "Инцидент печати",
  wireless_access_request: "Доступ к WLAN",
  employee_onboarding: "Онбординг сотрудника",
  access_revocation_request: "Отзыв доступа",
  workstation_unavailable_incident: "Недоступно рабочее место",
  workstation_hardware_diagnostic: "Диагностика оборудования",
  knowledge_request: "Консультация",
  non_it_request: "Непрофильное обращение",
  software_installation_request: "Установка программного обеспечения",
};

export const FACT_NAMES: Record<string, string> = {
  last_name: "Фамилия",
  first_name: "Имя",
  middle_name: "Отчество",
  department: "Подразделение",
  title: "Должность",
  phone: "Телефон",
  company: "Организация",
};

export const PREFLIGHT_ERROR_NAMES: Record<string, string> = {
  ad_credentials_unavailable: "Не настроено подключение к Active Directory",
  ad_secure_channel_required: "Для создания учётной записи требуется LDAPS",
  ad_target_ou_ambiguous: "Целевой контейнер OU не определён однозначно",
  ad_object_already_exists: "Учётная запись сотрудника уже существует",
  login_collision_limit: "Не удалось подобрать свободное имя пользователя",
  capability_disabled: "Выполнение этой операции отключено",
  capability_circuit_broken: "Исполнитель временно заблокирован после ошибок",
  capability_executor_unavailable: "Исполнитель операции недоступен",
};

export const CAPABILITY_NAMES: Record<string, string> = {
  create_ad_user: "Создать учётную запись в Active Directory",
  disable_ad_user: "Заблокировать учётную запись Active Directory",
  install_printer: "Установить принтер",
  install_printer_driver: "Установить драйвер принтера",
  configure_wlan: "Настроить доступ к WLAN",
};

export const DECISION_STATE_NAMES: Record<string, string> = {
  selected: "Тип определён",
  multi_intent: "Несколько сценариев",
  ambiguous: "Нужно уточнение",
  unknown: "Не определено",
  degraded: "Частичный анализ",
};

export function preflightUsable(item: PreflightItem): boolean {
  return ["passed", "not_applicable"].includes(item.status)
    && new Date(item.expires_at).getTime() > Date.now();
}

export function preflightStatusLabel(item: PreflightItem): string {
  if (["passed", "not_applicable"].includes(item.status) && !preflightUsable(item)) return "Проверка истекла";
  if (item.status === "passed") return "Готово";
  if (item.status === "not_applicable") return "Не требуется";
  if (item.status === "failed") return "Не пройдено";
  if (item.status === "degraded") return "Недоступно";
  return item.status;
}

export function detailText(details: Record<string, unknown> | undefined, key: string): string | null {
  const value = details?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export function latestFact(data: TicketAutomation, key: string): string | null {
  const sources = data.workflow_plan.fact_provenance?.[key] || [];
  const value = sources[sources.length - 1]?.value;
  return typeof value === "string" && value.trim() ? value : null;
}
