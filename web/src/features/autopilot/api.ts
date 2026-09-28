import type {
  ApprovalResponse,
  AutomationCommand,
  CatalogResponse,
  CorrectedActionInput,
  TicketAutomation,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  headers.set("Content-Type", "application/json");
  const auth = typeof window !== "undefined" ? window.localStorage.getItem("intralink_auth_b64") : null;
  if (auth && !headers.has("Authorization")) {
    headers.set("Authorization", `Basic ${auth}`);
  }
  const response = await fetch(url, {
    credentials: "include",
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const autopilotApi = {
  getAutomation(ticketId: number, signal?: AbortSignal) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/automation`, { signal });
  },
  analyze(ticketId: number) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/analyze`, { method: "POST" });
  },
  reanalyze(ticketId: number) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/reanalyze`, { method: "POST" });
  },
  approve(ticketId: number, automation: TicketAutomation) {
    if (!automation.action_plan) throw new Error("ActionPlan отсутствует");
    return request<ApprovalResponse>(`/api/v2/autopilot/tickets/${ticketId}/action-plans/approve`, {
      method: "POST",
      body: JSON.stringify({
        action_plan_id: automation.action_plan.id,
        plan_hash: automation.action_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
      }),
    });
  },
  correctCase(ticketId: number, automation: TicketAutomation, correctedCaseType: string, notes?: string) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/case-decision/correct`, {
      method: "POST",
      body: JSON.stringify({
        case_decision_id: automation.case_decision.id,
        snapshot_hash: automation.snapshot_hash,
        corrected_case_type: correctedCaseType,
        reason_tag: "operator_case_correction",
        notes,
      }),
    });
  },
  correctTargetService(ticketId: number, automation: TicketAutomation, targetServiceId: number, notes?: string) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/target-service/correct`, {
      method: "POST",
      body: JSON.stringify({
        compatibility_decision_id: automation.service_compatibility.id,
        snapshot_hash: automation.snapshot_hash,
        target_service_id: targetServiceId,
        reason_tag: "operator_target_correction",
        notes,
      }),
    });
  },
  correctActionPlan(ticketId: number, automation: TicketAutomation, actions: CorrectedActionInput[], notes?: string) {
    if (!automation.action_plan) throw new Error("ActionPlan отсутствует");
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/action-plans/correct`, {
      method: "POST",
      body: JSON.stringify({
        action_plan_id: automation.action_plan.id,
        plan_hash: automation.action_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        actions,
        reason_tag: "operator_action_correction",
        notes,
      }),
    });
  },
  reject(ticketId: number, automation: TicketAutomation, notes?: string) {
    if (!automation.action_plan) throw new Error("ActionPlan отсутствует");
    return request(`/api/v2/autopilot/tickets/${ticketId}/action-plans/reject`, {
      method: "POST",
      body: JSON.stringify({
        action_plan_id: automation.action_plan.id,
        plan_hash: automation.action_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        reason_tag: "operator_rejected",
        notes,
      }),
    });
  },
  manualTakeover(ticketId: number, automation: TicketAutomation, notes?: string) {
    if (!automation.action_plan) throw new Error("ActionPlan отсутствует");
    return request(`/api/v2/autopilot/tickets/${ticketId}/action-plans/manual-takeover`, {
      method: "POST",
      body: JSON.stringify({
        action_plan_id: automation.action_plan.id,
        plan_hash: automation.action_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        reason_tag: "manual_takeover",
        notes,
      }),
    });
  },
  approveRedirect(ticketId: number, automation: TicketAutomation) {
    if (!automation.redirect_plan) throw new Error("RedirectPlan отсутствует");
    return request(`/api/v2/autopilot/tickets/${ticketId}/redirect-plans/approve`, {
      method: "POST",
      body: JSON.stringify({
        redirect_plan_id: automation.redirect_plan.id,
        plan_hash: automation.redirect_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        version: automation.redirect_plan.version,
      }),
    });
  },
  correctRedirect(ticketId: number, automation: TicketAutomation, targetServiceId: number) {
    if (!automation.redirect_plan) throw new Error("RedirectPlan отсутствует");
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/redirect-plans/correct`, {
      method: "POST",
      body: JSON.stringify({
        redirect_plan_id: automation.redirect_plan.id,
        plan_hash: automation.redirect_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        version: automation.redirect_plan.version,
        target_service_id: targetServiceId,
        reason_tag: "operator_target_correction",
      }),
    });
  },
  stopRedirect(ticketId: number, automation: TicketAutomation, mode: "reject" | "manual-takeover") {
    if (!automation.redirect_plan) throw new Error("RedirectPlan отсутствует");
    return request(`/api/v2/autopilot/tickets/${ticketId}/redirect-plans/${mode}`, {
      method: "POST",
      body: JSON.stringify({
        redirect_plan_id: automation.redirect_plan.id,
        plan_hash: automation.redirect_plan.plan_hash,
        snapshot_hash: automation.snapshot_hash,
        version: automation.redirect_plan.version,
        reason_tag: mode === "reject" ? "operator_rejected" : "manual_takeover",
      }),
    });
  },
  getCaseTypes() {
    return request<CatalogResponse>("/api/v2/autopilot/case-types");
  },
  getWorkflows() {
    return request<CatalogResponse>("/api/v2/autopilot/workflows");
  },
  getCapabilities() {
    return request<CatalogResponse>("/api/v2/autopilot/capabilities");
  },
  getCommands(limit = 50) {
    return request<AutomationCommand[]>(`/api/v2/autopilot/commands?limit=${limit}`);
  },
  async getCommandStatus(commandId: string, signal?: AbortSignal) {
    return request<AutomationCommand>(`/api/v2/autopilot/commands/${commandId}`, { signal });
  },
};
