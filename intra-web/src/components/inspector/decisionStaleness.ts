export interface InitialDecisionVersion {
  taskId: number;
  version: number;
}

export function captureInitialDecisionVersion(
  current: InitialDecisionVersion | null,
  taskId: number,
  decisionTaskId?: number,
  decisionVersion?: number,
): InitialDecisionVersion | null {
  if (current?.taskId === taskId) return current;
  if (decisionTaskId !== taskId || !decisionVersion) return current;
  return { taskId, version: decisionVersion };
}

export function isDecisionVersionStale(
  explicitlyStale: boolean,
  current: InitialDecisionVersion | null,
  taskId: number,
  decisionVersion?: number,
): boolean {
  return Boolean(
    explicitlyStale ||
      (current?.taskId === taskId &&
        decisionVersion !== undefined &&
        decisionVersion > current.version),
  );
}
