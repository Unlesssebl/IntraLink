import type { TaskDetails } from './types';

// ==========================================
// TaskDetails In-Memory SWR Cache & In-Flight Registry
// ==========================================
interface CachedTaskDetails {
  data: TaskDetails;
  fetchedAt: number;
}

const TASK_DETAILS_CACHE_MAX_SIZE = 30;
const TASK_DETAILS_CACHE_TTL_MS = 2 * 60 * 1000; // 2 minutes

const taskDetailsCache = new Map<number, CachedTaskDetails>();
const inFlightTaskDetailsRequests = new Map<number, Promise<TaskDetails>>();

export function getCachedTaskDetails(taskId: number): TaskDetails | null {
  const entry = taskDetailsCache.get(taskId);
  if (!entry) return null;
  if (Date.now() - entry.fetchedAt > TASK_DETAILS_CACHE_TTL_MS) {
    taskDetailsCache.delete(taskId);
    return null;
  }
  // LRU Refresh: move to the end of insertion order
  taskDetailsCache.delete(taskId);
  taskDetailsCache.set(taskId, entry);
  return entry.data;
}

export function setCachedTaskDetails(taskId: number, data: TaskDetails): void {
  if (taskDetailsCache.size >= TASK_DETAILS_CACHE_MAX_SIZE) {
    const oldestKey = taskDetailsCache.keys().next().value;
    if (oldestKey !== undefined) {
      taskDetailsCache.delete(oldestKey);
    }
  }
  taskDetailsCache.set(taskId, {
    data,
    fetchedAt: Date.now(),
  });
}

export function invalidateTaskDetailsCache(taskId?: number): void {
  if (typeof taskId === 'number') {
    taskDetailsCache.delete(taskId);
  } else {
    taskDetailsCache.clear();
  }
}

export function getInFlightTaskDetailsRequest(taskId: number): Promise<TaskDetails> | undefined {
  return inFlightTaskDetailsRequests.get(taskId);
}

export function setInFlightTaskDetailsRequest(taskId: number, promise: Promise<TaskDetails>): void {
  inFlightTaskDetailsRequests.set(taskId, promise);
}

export function clearInFlightTaskDetailsRequest(taskId: number): void {
  inFlightTaskDetailsRequests.delete(taskId);
}
