import { getStoredAuth } from "@/shared/api";
import {
  AgentPlan,
  ApprovePlanRequest,
  AutopilotCommand,
  AutopilotPoliciesListResponse,
  AutopilotPolicy,
  AutopilotStats,
  CorrectPlanRequest,
  FeedbackListResponse,
  ManualTakeoverRequest,
  ManualTakeoverResponse,
  RejectPlanRequest,
  RoutingQualityMetrics,
  ScenarioCatalogResponse,
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

  async getPlan(ticketId: number, signal?: AbortSignal): Promise<AgentPlan> {
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

  async analyzePlan(ticketId: number): Promise<AgentPlan> {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/analyze`, {
      method: "POST",
      headers: getHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to analyze ticket: ${res.statusText}`);
    }
    return res.json();
  },

  async reanalyzePlan(ticketId: number): Promise<AgentPlan> {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/reanalyze`, {
      method: "POST",
      headers: getHeaders(),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Failed to reanalyze ticket: ${res.statusText}`);
    }
    return res.json();
  },

  async approvePlan(ticketId: number, req: ApprovePlanRequest) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/approve`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      const errorObj = new Error(err?.detail || `Failed to approve agent plan: ${res.statusText}`) as any;
      errorObj.status = res.status;
      errorObj.errorCode = res.headers.get("X-Error-Code");
      throw errorObj;
    }
    return res.json();
  },

  async correctPlan(ticketId: number, req: CorrectPlanRequest) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/correct`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      const errorObj = new Error(err?.detail || `Failed to correct agent plan: ${res.statusText}`) as any;
      errorObj.status = res.status;
      errorObj.errorCode = res.headers.get("X-Error-Code");
      throw errorObj;
    }
    return res.json();
  },

  async rejectPlan(ticketId: number, req: RejectPlanRequest) {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/reject`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      const errorObj = new Error(err?.detail || `Failed to reject plan: ${res.statusText}`) as any;
      errorObj.status = res.status;
      throw errorObj;
    }
    return res.json();
  },

  async manualTakeover(ticketId: number, req: ManualTakeoverRequest): Promise<ManualTakeoverResponse> {
    const res = await fetch(`/api/v2/autopilot/plan/${ticketId}/manual-takeover`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(req),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      const errorObj = new Error(err?.detail || `Failed to take over ticket: ${res.statusText}`) as any;
      errorObj.status = res.status;
      throw errorObj;
    }
    return res.json();
  },

  async getScenarioCatalog(): Promise<ScenarioCatalogResponse> {
    const res = await fetch("/api/v2/autopilot/scenarios/catalog", {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch scenario catalog: ${res.statusText}`);
    }
    return res.json();
  },

  async getFeedback(params?: {
    taskId?: number;
    verdict?: string;
    scenarioKey?: string;
    reasonTag?: string;
    limit?: number;
    offset?: number;
  }): Promise<FeedbackListResponse> {
    const q = new URLSearchParams();
    if (params?.taskId) q.set("task_id", String(params.taskId));
    if (params?.verdict) q.set("verdict", params.verdict);
    if (params?.scenarioKey) q.set("scenario_key", params.scenarioKey);
    if (params?.reasonTag) q.set("reason_tag", params.reasonTag);
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));

    const res = await fetch(`/api/v2/autopilot/feedback?${q.toString()}`, {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch feedback: ${res.statusText}`);
    }
    return res.json();
  },

  async exportFeedbackBlob(scenarioKey?: string, format: "jsonl" | "json" = "jsonl"): Promise<Blob> {
    const q = new URLSearchParams();
    q.set("format", format);
    if (scenarioKey) q.set("scenario_key", scenarioKey);

    const res = await fetch(`/api/v2/autopilot/feedback/export?${q.toString()}`, {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to export feedback dataset: ${res.statusText}`);
    }
    return res.blob();
  },

  async downloadFeedbackExport(scenarioKey?: string, format: "jsonl" | "json" = "jsonl"): Promise<void> {
    const blob = await this.exportFeedbackBlob(scenarioKey, format);
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `routing_feedback_dataset.${format}`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  },

  async getQualityMetrics(params?: {
    scenarioKey?: string;
    routerVersion?: string;
    promptVersion?: string;
    routingState?: string;
  }): Promise<{ metrics: RoutingQualityMetrics }> {
    const q = new URLSearchParams();
    if (params?.scenarioKey) q.set("scenario_key", params.scenarioKey);
    if (params?.routerVersion) q.set("router_version", params.routerVersion);
    if (params?.promptVersion) q.set("prompt_version", params.promptVersion);
    if (params?.routingState) q.set("routing_state", params.routingState);

    const res = await fetch(`/api/v2/autopilot/metrics/quality?${q.toString()}`, {
      headers: getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch quality metrics: ${res.statusText}`);
    }
    return res.json();
  },

  async getCommandStatus(commandId: string, signal?: AbortSignal) {
    const res = await fetch(`/api/v2/tasks/${commandId}`, {
      headers: getHeaders(),
      signal,
    });
    if (!res.ok) {
      throw new Error(`Failed to fetch command status: ${res.statusText}`);
    }
    return res.json();
  },

  async batchAssign(ticketIds: number[]) {
    const res = await fetch("/api/v2/autopilot/batch-assign", {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify({ ticket_ids: ticketIds }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      throw new Error(err?.detail || `Batch assign failed: ${res.statusText}`);
    }
    return res.json();
  },
};
