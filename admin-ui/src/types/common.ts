export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastNotification {
  id: number;
  message: string;
  type: ToastType;
  title: string | null;
  duration: number;
  action: ToastAction | null;
  createdAt: number;
}

export interface StatsItem {
  num: number | string;
  desc: string;
  icon: string;
  color: 'green' | 'blue' | 'red' | 'yellow' | 'purple';
}

export interface UserSession {
  username: string;
}
