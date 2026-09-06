import { apiFetch } from './api';
import type { DecisionFeedbackPayload, DecisionFeedbackRecord, DecisionRecord } from './types';

export interface TaskDecisionsResponse {
  items: DecisionRecord[];
  next_before_version: number | null;
}

export async function fetchTaskDecisions(
  taskId: number,
  limit: number = 20,
  beforeVersion?: number
): Promise<TaskDecisionsResponse> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (beforeVersion !== undefined) {
    query.set('before_version', String(beforeVersion));
  }
  return apiFetch<TaskDecisionsResponse>(`/api/v2/tasks/${taskId}/decisions?${query.toString()}`);
}

export async function fetchDecisionDetails(decisionId: string): Promise<DecisionRecord> {
  return apiFetch<DecisionRecord>(`/api/v2/decisions/${decisionId}`);
}

export async function submitDecisionFeedback(
  decisionId: string,
  payload: DecisionFeedbackPayload
): Promise<DecisionFeedbackRecord> {
  return apiFetch<DecisionFeedbackRecord>(`/api/v2/decisions/${decisionId}/feedback`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
