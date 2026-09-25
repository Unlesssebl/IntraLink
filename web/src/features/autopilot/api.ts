import { getStoredAuth } from "@/shared/api";
import {
  AutopilotCommand,
  AutopilotPoliciesListResponse,
  AutopilotPolicy,
  AutopilotStats,
  UpdateAutopilotPolicyRequest,
} from "./types";

function getHeaders(): HeadersInit {
  const auth = getStoredAuth();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (auth) {
    headers["Authorization"] = `Basic ${auth}`;
  }
  return headers;
}

export const autopilotApi = {
  async getPolicies(): Promise<AutopilotPoliciesListResponse> {
    const res = await fetch("/api/v2/autopilot/policies", {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch autopilot policies: ${res.statusText}`);
    }
    return res.json();
  },

  async updatePolicy(
    scenarioKey: string,
    req: UpdateAutopilotPolicyRequest
  ): Promise<AutopilotPolicy> {
    const res = await fetch(`/api/v2/autopilot/policies/${scenarioKey}`, {
      method: "PUT",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      throw new Error(`Failed to update policy: ${res.statusText}`);
    }
    return res.json();
  },

  async resetCircuitBreaker(scenarioKey: string): Promise<AutopilotPolicy> {
    const res = await fetch(`/api/v2/autopilot/policies/${scenarioKey}/reset`, {
      method: "POST",
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to reset circuit breaker: ${res.statusText}`);
    }
    return res.json();
  },

  async getCommands(limit = 50): Promise<AutopilotCommand[]> {
    const res = await fetch(`/api/v2/autopilot/commands?limit=${limit}`, {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch autopilot commands: ${res.statusText}`);
    }
    return res.json();
  },

  async getStats(): Promise<AutopilotStats> {
    const res = await fetch("/api/v2/autopilot/stats", {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch autopilot stats: ${res.statusText}`);
    }
    return res.json();
  },

  async getPlan(ticketId: number, signal?: AbortSignal) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}`, {
      headers: getHeaders(),
      signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to fetch agent plan: ${res.statusText}`);
    }
    return res.json();
  },

  async approvePlan(ticketId: number, req: import("./types").ApprovePlanRequest) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/approve`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to approve agent plan: ${res.statusText}`);
    }
    return res.json();
  },

  async correctPlan(ticketId: number, req: import("./types").CorrectPlanRequest) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/correct`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to correct agent plan: ${res.statusText}`);
    }
    return res.json();
  },

  async getCorrections(limit = 100, tag?: string) {
    const q = new URLSearchParams({ limit: String(limit) });
    if (tag) q.set("tag", tag);
    const res = await fetch(`/api/v2/autopilot/corrections?${q.toString()}`, {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch corrections: ${res.statusText}`);
    }
    return res.json();
  },

  getExportCorrectionsUrl(limit = 500): string {
    const auth = getStoredAuth();
    const query = auth ? `?limit=${limit}&auth_b64=${encodeURIComponent(auth)}` : `?limit=${limit}`;
    return `/api/v2/autopilot/corrections/export${query}`;
  },

  async getCommandStatus(commandId: string, signal?: AbortSignal) {
    const res = await fetch(`/api/v2/tasks/${commandId}`, {
      headers: getHeaders(),
      signal,
    });
    if (!res.ok) {
      throw new Error(`Failed to poll task status: ${res.statusText}`);
    }
    return res.json();
  },

  async batchAssign(ticketIds: number[]): Promise<import("./types").BatchAssignResponse> {
    const res = await fetch("/api/v2/autopilot/batch-assign", {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify({ ticket_ids: ticketIds }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to batch assign: ${res.statusText}`);
    }
    return res.json();
  },
};
