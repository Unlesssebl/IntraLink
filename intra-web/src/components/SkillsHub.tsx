import React, { useState, useEffect, useRef } from 'react';
import {
  fetchSkills,
  updateSkillPolicy,
  resetSkillPolicy,
  type SkillActionItem,
} from '../lib/adminApi';
import { submitCommand } from '../lib/tasks';

interface SkillsHubProps {
  token: string;
}

interface StreamEventItem {
  id: string;
  timestamp: string;
  eventType: string;
  jobId?: string;
  data: any;
}

export default function SkillsHub({ token }: SkillsHubProps) {
  const [skills, setSkills] = useState<SkillActionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  // Test Run Modal State
  const [testAction, setTestAction] = useState<SkillActionItem | null>(null);
  const [testTargetInput, setTestTargetInput] = useState('');
  const [testRunning, setTestRunning] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);

  // Live SSE Terminal State
  const [events, setEvents] = useState<StreamEventItem[]>([]);
  const [sseConnected, setSseConnected] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  const loadSkills = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await fetchSkills(token);
      setSkills(list);
    } catch (err: any) {
      setError(err.message || 'Не удалось загрузить каталог действий');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSkills();
  }, [token]);

  // Server-Sent Events (SSE) listener
  useEffect(() => {
    const es = new EventSource('/api/v1/events/stream');

    es.onopen = () => {
      setSseConnected(true);
    };

    es.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data);
        const item: StreamEventItem = {
          id: `${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          timestamp: new Date().toLocaleTimeString(),
          eventType: parsed.event_type || parsed.type || 'message',
          jobId: parsed.job_id,
          data: parsed,
        };
        setEvents((prev) => [...prev.slice(-100), item]);
      } catch {
        // Raw text event
        setEvents((prev) => [
          ...prev.slice(-100),
          {
            id: `${Date.now()}`,
            timestamp: new Date().toLocaleTimeString(),
            eventType: 'raw',
            data: event.data,
          },
        ]);
      }
    };

    es.onerror = () => {
      setSseConnected(false);
    };

    return () => {
      es.close();
    };
  }, []);

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const handlePolicyChange = async (
    actionId: string,
    newMode: 'auto' | 'confirm' | 'disabled'
  ) => {
    setUpdatingId(actionId);
    try {
      await updateSkillPolicy(token, actionId, newMode);
      setSkills((prev) =>
        prev.map((s) =>
          s.id === actionId ? { ...s, effective_mode: newMode } : s
        )
      );
    } catch (err: any) {
      alert(err.message || 'Ошибка обновления политики');
    } finally {
      setUpdatingId(null);
    }
  };

  const handleResetPolicy = async (actionId: string) => {
    setUpdatingId(actionId);
    try {
      await resetSkillPolicy(token, actionId);
      await loadSkills();
    } catch (err: any) {
      alert(err.message || 'Ошибка сброса политики');
    } finally {
      setUpdatingId(null);
    }
  };

  const handleRunTest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!testAction) return;
    setTestRunning(true);
    setTestResult(null);

    try {
      let targetObj: Record<string, any> = {};
      try {
        targetObj = JSON.parse(testTargetInput);
      } catch {
        targetObj = { target: testTargetInput.trim() };
      }

      const res = await submitCommand({
        type: testAction.id,
        target: targetObj,
        mode: testAction.effective_mode === 'disabled' ? 'auto' : (testAction.effective_mode as any),
      });

      setTestResult(`Команда отправлена в Command Bus: Job ID = ${res.job_id}`);
    } catch (err: any) {
      setTestResult(`Ошибка: ${err.message || 'Не удалось запустить команду'}`);
    } finally {
      setTestRunning(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-neutral-900/60 p-6 rounded-2xl border border-neutral-800">
        <div>
          <h2 className="text-lg font-semibold text-neutral-100 flex items-center gap-2.5">
            <span className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse"></span>
            Центр управления навыками и политиками (Skills Hub)
          </h2>
          <p className="text-xs text-neutral-400 mt-1">
            Управление поведением инфраструктурных исполнителей: Автономный режим (Auto), подтверждение оператора (HitL) и аварийный Killswitch.
          </p>
        </div>
        <button
          onClick={loadSkills}
          disabled={loading}
          className="px-4 py-2 text-xs font-medium text-neutral-300 bg-neutral-800 hover:bg-neutral-700 rounded-xl border border-neutral-700 transition-colors flex items-center gap-2 cursor-pointer self-start md:self-auto"
        >
          <svg
            className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
          </svg>
          <span>Обновить</span>
        </button>
      </div>

      {error && (
        <div className="p-4 rounded-xl bg-red-950/40 border border-red-800 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Skills Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {skills.map((skill) => {
          const isKillswitch = skill.effective_mode === 'disabled';
          const isHitl = skill.effective_mode === 'confirm';
          const isAuto = skill.effective_mode === 'auto';
          const isUpdating = updatingId === skill.id;

          return (
            <div
              key={skill.id}
              className={`p-5 rounded-2xl border transition-all ${
                isKillswitch
                  ? 'bg-red-950/10 border-red-900/40'
                  : 'bg-neutral-950 border-neutral-800 hover:border-neutral-700'
              }`}
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-neutral-100">
                      {skill.name}
                    </span>
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase font-semibold bg-neutral-800 text-neutral-400 border border-neutral-700">
                      {skill.category}
                    </span>
                  </div>
                  <span className="text-[11px] text-neutral-500 font-mono">
                    ID: {skill.id} • Target: {skill.target_type}
                  </span>
                </div>

                {/* Status Indicator */}
                <div className="flex items-center gap-1.5">
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${
                      isKillswitch
                        ? 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]'
                        : isHitl
                        ? 'bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.6)]'
                        : 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]'
                    }`}
                  />
                  <span
                    className={`text-xs font-semibold uppercase font-mono ${
                      isKillswitch
                        ? 'text-red-400'
                        : isHitl
                        ? 'text-amber-400'
                        : 'text-emerald-400'
                    }`}
                  >
                    {skill.effective_mode}
                  </span>
                </div>
              </div>

              <p className="text-xs text-neutral-400 mb-4 leading-relaxed line-clamp-2">
                {skill.description}
              </p>

              {/* Policy Mode Selector Bar */}
              <div className="pt-3 border-t border-neutral-800/80 flex items-center justify-between gap-2">
                <div className="inline-flex rounded-lg bg-neutral-900 p-1 border border-neutral-800 text-xs">
                  <button
                    onClick={() => handlePolicyChange(skill.id, 'auto')}
                    disabled={isUpdating}
                    className={`px-2.5 py-1 rounded font-medium transition-colors cursor-pointer ${
                      isAuto
                        ? 'bg-emerald-600 text-white shadow-sm'
                        : 'text-neutral-400 hover:text-neutral-200'
                    }`}
                  >
                    Auto
                  </button>
                  <button
                    onClick={() => handlePolicyChange(skill.id, 'confirm')}
                    disabled={isUpdating}
                    className={`px-2.5 py-1 rounded font-medium transition-colors cursor-pointer ${
                      isHitl
                        ? 'bg-amber-600 text-white shadow-sm'
                        : 'text-neutral-400 hover:text-neutral-200'
                    }`}
                  >
                    HitL
                  </button>
                  <button
                    onClick={() => handlePolicyChange(skill.id, 'disabled')}
                    disabled={isUpdating}
                    className={`px-2.5 py-1 rounded font-medium transition-colors cursor-pointer ${
                      isKillswitch
                        ? 'bg-red-600 text-white shadow-sm'
                        : 'text-neutral-400 hover:text-neutral-200'
                    }`}
                  >
                    Killswitch
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      setTestAction(skill);
                      setTestTargetInput(
                        skill.target_type === 'host'
                          ? '{"pc_name": "WS-OFFICE01", "printer_name": "HP LaserJet M402"}'
                          : skill.target_type === 'user'
                          ? '{"identity": "test.user"}'
                          : '{"task_id": 101}'
                      );
                      setTestResult(null);
                    }}
                    className="px-2.5 py-1 text-xs font-medium text-blue-400 hover:text-blue-300 hover:bg-blue-950/30 rounded border border-blue-900/50 transition-colors cursor-pointer"
                  >
                    Тест
                  </button>

                  <button
                    onClick={() => handleResetPolicy(skill.id)}
                    title="Сбросить к умолчанию"
                    className="p-1 text-neutral-500 hover:text-neutral-300 rounded hover:bg-neutral-800 transition-colors cursor-pointer"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
                      <path d="M3 3v5h5" />
                    </svg>
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Live Event Terminal (SSE) */}
      <div className="bg-neutral-950 rounded-2xl border border-neutral-800 overflow-hidden shadow-2xl">
        <div className="flex items-center justify-between px-5 py-3.5 bg-neutral-900/80 border-b border-neutral-800">
          <div className="flex items-center gap-2.5">
            <span
              className={`w-2 h-2 rounded-full ${
                sseConnected ? 'bg-emerald-500 animate-pulse' : 'bg-red-500'
              }`}
            />
            <span className="text-xs font-mono font-semibold text-neutral-200">
              Live Event Bus & Execution Stream (/api/v1/events/stream)
            </span>
            <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-neutral-800 text-neutral-400 border border-neutral-700">
              {sseConnected ? 'CONNECTED' : 'DISCONNECTED'}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setEvents([])}
              className="text-xs text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800 px-2 py-1 rounded transition-colors cursor-pointer"
            >
              Очистить
            </button>
          </div>
        </div>

        <div className="p-4 font-mono text-xs text-neutral-300 space-y-1.5 h-64 overflow-y-auto bg-black/40">
          {events.length === 0 ? (
            <div className="text-neutral-500 italic py-8 text-center">
              Ожидание входящих событий от воркеров и шины Command Bus...
            </div>
          ) : (
            events.map((ev) => (
              <div key={ev.id} className="flex items-start gap-2 leading-relaxed">
                <span className="text-neutral-500 select-none">[{ev.timestamp}]</span>
                <span className="text-blue-400 font-semibold">{ev.eventType}:</span>
                <span className="text-neutral-200 break-all">
                  {typeof ev.data === 'string'
                    ? ev.data
                    : JSON.stringify(ev.data)}
                </span>
              </div>
            ))
          )}
          <div ref={terminalEndRef} />
        </div>
      </div>

      {/* Test Runner Modal */}
      {testAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg bg-neutral-950 border border-neutral-800 rounded-2xl p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
              <h3 className="text-sm font-semibold text-neutral-100 flex items-center gap-2">
                <span>⚡ Тестовый запуск:</span>
                <span className="text-blue-400 font-mono">{testAction.id}</span>
              </h3>
              <button
                onClick={() => setTestAction(null)}
                className="text-neutral-500 hover:text-neutral-300 cursor-pointer"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-neutral-400">{testAction.description}</p>

            <form onSubmit={handleRunTest} className="space-y-3">
              <div>
                <label className="block text-xs font-mono text-neutral-300 mb-1">
                  Целевой объект (Target JSON):
                </label>
                <textarea
                  value={testTargetInput}
                  onChange={(e) => setTestTargetInput(e.target.value)}
                  rows={3}
                  className="w-full rounded-lg bg-neutral-900 border border-neutral-800 p-2.5 text-xs font-mono text-neutral-200 focus:outline-none focus:border-blue-500"
                  required
                />
              </div>

              {testResult && (
                <div
                  className={`p-3 rounded-lg text-xs font-mono ${
                    testResult.startsWith('Ошибка')
                      ? 'bg-red-950/40 border border-red-800 text-red-300'
                      : 'bg-emerald-950/40 border border-emerald-800 text-emerald-300'
                  }`}
                >
                  {testResult}
                </div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setTestAction(null)}
                  className="px-3 py-1.5 rounded-lg text-xs text-neutral-400 hover:bg-neutral-800 cursor-pointer"
                >
                  Закрыть
                </button>
                <button
                  type="submit"
                  disabled={testRunning}
                  className="px-4 py-1.5 rounded-lg text-xs font-semibold bg-blue-600 hover:bg-blue-500 text-white transition-colors cursor-pointer"
                >
                  {testRunning ? 'Отправка...' : 'Отправить в Command Bus'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
