import { apiFetch } from './api';

export type DesktopClient = 'litemanager' | 'dameware' | 'rdp';

const fallbackCommands: Record<DesktopClient, (host: string) => string> = {
  litemanager: host => `romviewer.exe /connect:${host}`,
  dameware: host => `dwrcc.exe -c: -m:${host}`,
  rdp: host => `mstsc.exe /v:${host}`,
};

export function getDesktopFallbackCommand(client: DesktopClient, host: string) {
  return fallbackCommands[client](host);
}

export async function launchDesktopClient(taskId: number, host: string, client: DesktopClient): Promise<void> {
  const result = await apiFetch<{ deep_link: string }>('/api/v1/desktop/launches', {
    method: 'POST', body: JSON.stringify({ task_id: taskId, host, client }),
  });
  window.location.assign(result.deep_link);
}

export async function launchDesktopClientWithFallback(
  taskId: number,
  host: string,
  client: DesktopClient,
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void
): Promise<void> {
  const label = client === 'litemanager' ? 'LiteManager' : client === 'dameware' ? 'DameWare' : 'RDP';
  try {
    await launchDesktopClient(taskId, host, client);
    onToast({ type: 'info', message: `${label}: запрос передан Desktop Companion` });
  } catch {
    const command = getDesktopFallbackCommand(client, host);
    await navigator.clipboard.writeText(command);
    onToast({
      type: 'warning',
      message: `Desktop Companion недоступен. Команда скопирована: ${command}`,
    });
  }
}

