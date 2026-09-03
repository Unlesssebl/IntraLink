import { useEffect, useRef, useState, useCallback } from 'react';

export interface LiveEventPayload {
  event: string;
  job_id?: string;
  task_id?: number;
  task_ids?: number[];
  status_id?: number;
  data?: any;
  timestamp?: number;
  [key: string]: any;
}

interface UseLiveEventsOptions {
  enabled?: boolean;
  onEvent?: (event: LiveEventPayload) => void;
  onQueueRefreshNeeded?: (reason: string) => void;
  onTaskStatusUpdated?: (taskIds: number[], newStatusId: number) => void;
  onConfirmRequired?: (jobId: string, prompt: string, details: any) => void;
  onOutageEvent?: (event: LiveEventPayload) => void;
}

export function useLiveEvents({
  enabled = true,
  onEvent,
  onQueueRefreshNeeded,
  onTaskStatusUpdated,
  onConfirmRequired,
  onOutageEvent,
}: UseLiveEventsOptions = {}) {
  const [isConnected, setIsConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<LiveEventPayload | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const retryDelayRef = useRef(1000);

  const connect = useCallback(() => {
    if (!enabled) return;
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    const token = localStorage.getItem('intralink_admin_token');
    const url = `/api/v1/events/stream?channel=all${token ? `&token=${encodeURIComponent(token)}` : ''}`;

    try {
      const es = new EventSource(url, { withCredentials: true });
      eventSourceRef.current = es;

      es.onopen = () => {
        setIsConnected(true);
        retryDelayRef.current = 1000;
      };

      const handleMessagePayload = (dataStr: string) => {
        try {
          const parsed = JSON.parse(dataStr);
          const eventType = parsed.event || parsed.type || 'message';
          const payload: LiveEventPayload = { ...parsed, event: eventType };

          setLastEvent(payload);
          onEvent?.(payload);

          // 1. Обработка применения триажа или смены статуса заявки
          if (eventType === 'triage_applied' && Array.isArray(payload.task_ids) && payload.status_id) {
            onTaskStatusUpdated?.(payload.task_ids, payload.status_id);
            onQueueRefreshNeeded?.('triage_applied');
          } else if ((eventType === 'status_change' || eventType === 'new_task') && payload.task_id) {
            if (payload.status_id) {
              onTaskStatusUpdated?.([payload.task_id], payload.status_id);
            }
            onQueueRefreshNeeded?.(eventType);
          }

          // 2. Обработка завершения фоновой задачи воркера
          if ((eventType === 'success' || eventType === 'failed') && payload.data) {
            const taskId = payload.data.task_id || payload.task_id;
            if (taskId && eventType === 'success') {
              onTaskStatusUpdated?.([taskId], 29);
            }
            onQueueRefreshNeeded?.(`worker_${eventType}`);
          }

          // 3. Обработка запроса HitL-подтверждения
          if (eventType === 'confirm_required' && payload.data) {
            const jobId = payload.job_id || 'unknown';
            const prompt = payload.data.prompt || 'Требуется подтверждение действия';
            onConfirmRequired?.(jobId, prompt, payload.data.details);
          }

          // 4. Обработка событий массовых инцидентов (AIOps Outages)
          if (eventType === 'outage_detected' || eventType === 'outage_updated' || eventType === 'outage_resolved') {
            onOutageEvent?.(payload);
          }
        } catch (e) {
          console.debug('[LiveEvents] Ошибка парсинга события:', e);
        }
      };

      es.onmessage = (e) => {
        handleMessagePayload(e.data);
      };

      // Слушаем именованные события SSE
      const eventNames = [
        'connected',
        'started',
        'progress',
        'success',
        'failed',
        'confirm_required',
        'triage_applied',
        'status_change',
        'new_task',
        'new_comment',
        'outage_detected',
        'outage_updated',
        'outage_resolved',
      ];
      eventNames.forEach((evName) => {
        es.addEventListener(evName, (e: any) => {
          handleMessagePayload(e.data);
        });
      });

      es.onerror = () => {
        setIsConnected(false);
        if (es.readyState === EventSource.CLOSED) {
          es.close();
          eventSourceRef.current = null;
          // Экспоненциальный бэкофф переподключения (до 15 сек)
          const delay = Math.min(15000, retryDelayRef.current * 1.5);
          retryDelayRef.current = delay;
          reconnectTimeoutRef.current = window.setTimeout(connect, delay);
        }
      };
    } catch (err) {
      console.warn('[LiveEvents] Не удалось создать EventSource:', err);
      setIsConnected(false);
    }
  }, [enabled, onEvent, onQueueRefreshNeeded, onTaskStatusUpdated, onConfirmRequired]);

  useEffect(() => {
    if (enabled) {
      connect();
    } else {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setIsConnected(false);
    }

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setIsConnected(false);
    };
  }, [enabled, connect]);

  return { isConnected, lastEvent };
}
