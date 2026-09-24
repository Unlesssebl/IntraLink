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
};
