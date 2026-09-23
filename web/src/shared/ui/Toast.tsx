import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from "lucide-react";

export type ToastType = "success" | "error" | "warning" | "info";

export interface ToastItem {
  id: string;
  type: ToastType;
  message: string;
  duration?: number;
}

interface ToastContextValue {
  toasts: ToastItem[];
  showToast: (type: ToastType, message: string, duration?: number) => void;
  dismissToast: (id: string) => void;
  success: (message: string, duration?: number) => void;
  error: (message: string, duration?: number) => void;
  warning: (message: string, duration?: number) => void;
  info: (message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

const toastIcons: Record<ToastType, React.ReactNode> = {
  success: <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" strokeWidth={1.5} />,
  error: <AlertCircle className="w-4 h-4 text-rose-400 shrink-0 mt-0.5" strokeWidth={1.5} />,
  warning: <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" strokeWidth={1.5} />,
  info: <Info className="w-4 h-4 text-blue-400 shrink-0 mt-0.5" strokeWidth={1.5} />,
};

const toastBorderStyles: Record<ToastType, string> = {
  success: "border-neutral-800",
  error: "border-rose-900/60",
  warning: "border-amber-900/60",
  info: "border-neutral-800",
};

export const Toast: React.FC<{
  toast: ToastItem;
  onDismiss: (id: string) => void;
}> = ({ toast, onDismiss }) => {
  useEffect(() => {
    const timer = setTimeout(() => {
      onDismiss(toast.id);
    }, toast.duration || 4000);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 px-3.5 py-3 rounded border text-xs font-medium shadow-xl min-w-[280px] max-w-[400px] bg-neutral-900 text-white select-none transition-all animate-in fade-in slide-in-from-bottom-2 duration-150 ${
        toastBorderStyles[toast.type]
      }`}
    >
      {toastIcons[toast.type]}
      <span className="flex-1 leading-snug text-neutral-200">{toast.message}</span>
      <button
        onClick={() => onDismiss(toast.id)}
        className="shrink-0 text-neutral-400 hover:text-white transition-colors p-0.5 rounded hover:bg-neutral-800"
        title="Закрыть"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
};

export const ToastContainer: React.FC<{
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}> = ({ toasts, onDismiss }) => {
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <Toast toast={t} onDismiss={onDismiss} />
        </div>
      ))}
    </div>
  );
};

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (type: ToastType, message: string, duration?: number) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setToasts((prev) => [...prev, { id, type, message, duration }]);
    },
    []
  );

  const success = useCallback(
    (message: string, duration?: number) => showToast("success", message, duration),
    [showToast]
  );
  const error = useCallback(
    (message: string, duration?: number) => showToast("error", message, duration),
    [showToast]
  );
  const warning = useCallback(
    (message: string, duration?: number) => showToast("warning", message, duration),
    [showToast]
  );
  const info = useCallback(
    (message: string, duration?: number) => showToast("info", message, duration),
    [showToast]
  );

  return (
    <ToastContext.Provider
      value={{
        toasts,
        showToast,
        dismissToast,
        success,
        error,
        warning,
        info,
      }}
    >
      {children}
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </ToastContext.Provider>
  );
};

export const useToast = (): ToastContextValue => {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
};
