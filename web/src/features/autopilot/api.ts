import type {
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
    throw new AutopilotApiError(body?.detail || `HTTP ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export class AutopilotApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "AutopilotApiError";
  }
}

export const autopilotApi = {
  getAutomation(ticketId: number, signal?: AbortSignal) {
    return request<TicketAutomation | null>(`/api/v2/autopilot/tickets/${ticketId}/automation`, { signal });
  },
  analyze(ticketId: number) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/analyze`, { method: "POST" });
  },
  reanalyze(ticketId: number) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/reanalyze`, { method: "POST" });
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
  correctFacts(ticketId: number, automation: TicketAutomation, facts: Record<string, string>) {
    return request<TicketAutomation>(`/api/v2/autopilot/tickets/${ticketId}/facts/correct`, {
      method: "POST",
      body: JSON.stringify({
        snapshot_hash: automation.snapshot_hash,
        facts,
        reason_tag: "operator_fact_confirmation",
      }),
    });
  },
  correctOnboardingFacts(ticketId: number, automation: TicketAutomation, facts: Record<string, string>) {
    return this.correctFacts(ticketId, automation, facts);
  },
  retryClarification(ticketId: number, automation: TicketAutomation, clarificationId: string) {
    return request<TicketAutomation>(
      `/api/v2/autopilot/tickets/${ticketId}/clarifications/${clarificationId}/retry`,
      {
        method: "POST",
        body: JSON.stringify({ snapshot_hash: automation.snapshot_hash }),
      }
    );
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
  resolveHumanAction(
    ticketId: number,
    humanActionId: string,
    candidateId: string,
    snapshotHash?: string,
    planHash?: string,
    generation?: number
  ) {
    return request<TicketAutomation>(
      `/api/v2/autopilot/tickets/${ticketId}/human-actions/${humanActionId}/resolve`,
      {
        method: "POST",
        body: JSON.stringify({
          candidate_id: candidateId,
          snapshot_hash: snapshotHash,
          plan_hash: planHash,
          generation,
        }),
      }
    );
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
