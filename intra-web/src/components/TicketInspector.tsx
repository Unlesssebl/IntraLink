import { useState, useEffect, useCallback, useMemo } from 'react';
import type { Ticket } from '../data/mock';
import {
  fetchDiagnostics,
  fetchTaskDetails,
  fetchTemplatesCatalog,
  fetchTicketSummary,
  getCachedTaskDetails,
  setCachedTaskDetails,
} from '../lib/tasks';
import type { TaskDetails, TicketSummaryResult, HostDiagnostics } from '../lib/types';
import InspectorHeader from './inspector/InspectorHeader';
import AttachmentsSection from './inspector/AttachmentsSection';
import CommentsTimeline from './inspector/CommentsTimeline';
import UnifiedDecisionPanel from './inspector/UnifiedDecisionPanel';
import UnifiedActionDock from './inspector/UnifiedActionDock';
import TicketContextSummary from './inspector/TicketContextSummary';
import InspectorWorkspace from './inspector/InspectorWorkspace';
import type { DiagStatus } from '../lib/types';
import { useUnifiedDecision } from './inspector/useUnifiedDecision';
import { useResolvedEntities } from './inspector/useResolvedEntities';
import { filterMeaningfulComments } from './inspector/commentsUtils';

interface Props {
  ticket: Ticket;
  onClose: () => void;
  onUpdateTicket: (id: string, changes: Partial<Ticket>) => void;
  onToast: (t: { type: 'success' | 'error' | 'warning' | 'info'; message: string }) => void;
}

export default function TicketInspector({ ticket, onClose, onUpdateTicket, onToast }: Props) {
  const rawId = ticket.rawId || parseInt(ticket.id.replace(/\D/g, ''), 10);
  const initialCached = useMemo(() => (rawId ? getCachedTaskDetails(rawId) : null), [rawId]);
  const [details, setDetails] = useState<TaskDetails | null>(() => initialCached);
  const [loadingDetails, setLoadingDetails] = useState<boolean>(() => !Boolean(initialCached));
  const [expanded, setExpanded] = useState<boolean>(() => {
    return localStorage.getItem('intralink_inspector_expanded') === 'true';
  });

  const toggleExpanded = () => {
    setExpanded((prev) => {
      const next = !prev;
      localStorage.setItem('intralink_inspector_expanded', String(next));
      return next;
    });
  };

  const [templates, setTemplates] = useState<any[]>([]);
  const [diagStatus, setDiagStatus] = useState<Record<string, DiagStatus>>({
    ping: 'idle',
    smb: 'idle',
    winrm: 'idle',
  });
  const [hostDiagnostics, setHostDiagnostics] = useState<HostDiagnostics | null>(null);
  const [loadingDiag, setLoadingDiag] = useState<boolean>(false);

  // AI Summary State
  const [aiSummary, setAiSummary] = useState<TicketSummaryResult | null>(null);
  const [loadingAiSummary, setLoadingAiSummary] = useState(false);
  const [isCommentsExpanded, setIsCommentsExpanded] = useState(true);

  // Resizing state
  const [inspectorWidth, setInspectorWidth] = useState<number>(() => {
    const saved = localStorage.getItem('intralink_inspector_width');
    const parsed = saved ? parseInt(saved, 10) : 600;
    return !isNaN(parsed) && parsed >= 440 ? parsed : 600;
  });
  const [isResizing, setIsResizing] = useState(false);

  // Drag-to-resize listener
  useEffect(() => {
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      const maxW = Math.min(1200, window.innerWidth * 0.92);
      const minW = 440;
      const newWidth = Math.max(minW, Math.min(maxW, window.innerWidth - e.clientX));
      setInspectorWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      localStorage.setItem('intralink_inspector_width', String(inspectorWidth));
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizing, inspectorWidth]);

  // Guard against details from previous ticket during async fetch
  const isDetailsForCurrentTicket = Boolean(
    details &&
      (details.id === rawId ||
        (details as any).task?.id === rawId ||
        (details as any).task?.Id === rawId)
  );
  const safeDetails = isDetailsForCurrentTicket ? details : null;
  const resolvedEntities = useResolvedEntities(ticket, safeDetails);
  const hostList = resolvedEntities.host.hostList;
  const effectiveHost = resolvedEntities.host.primaryHost;

  // Load Task Details from Core API
  const loadDetails = useCallback(
    async (updated?: TaskDetails) => {
      if (!rawId) return;
      if (updated) {
        if (
          updated.id === rawId ||
          (updated as any).task?.id === rawId ||
          (updated as any).task?.Id === rawId
        ) {
          setDetails(updated);
          setCachedTaskDetails(rawId, updated);
        }
        return;
      }
      setLoadingDetails(true);
      try {
        const data = await fetchTaskDetails(rawId, { bypassCache: true });
        setDetails(data);
      } catch (err: any) {
        console.warn('Не удалось загрузить подробности заявки:', err);
      } finally {
        setLoadingDetails(false);
      }
    },
    [rawId]
  );

  // Load Task Details with SWR & cancellation
  useEffect(() => {
    let cancelled = false;
    if (!rawId) {
      setDetails(null);
      setLoadingDetails(false);
      setHostDiagnostics(null);
      setDiagStatus({ ping: 'idle', smb: 'idle', winrm: 'idle' });
      return;
    }

    const cached = getCachedTaskDetails(rawId);
    if (cached) {
      setDetails(cached);
      setLoadingDetails(false);
      if (cached.telemetry) {
        const t = cached.telemetry;
        setHostDiagnostics({
          host: t.canonical_name || t.pc_name,
          resolved_ip: t.resolved_ip,
          is_online: t.status === 'ONLINE',
          avg_rtt: t.avg_rtt,
          smb_ok: t.smb_port_445,
          winrm_ok: t.winrm_port_5985,
          specs: t.specs,
        });
      }
    } else {
      setDetails(null);
      setLoadingDetails(true);
      setHostDiagnostics(null);
      setDiagStatus({ ping: 'idle', smb: 'idle', winrm: 'idle' });
    }
    setAiSummary(null);

    fetchTaskDetails(rawId)
      .then((data) => {
        if (!cancelled && data) {
          setDetails(data);
          if (data.telemetry) {
            const t = data.telemetry;
            setHostDiagnostics({
              host: t.canonical_name || t.pc_name,
              resolved_ip: t.resolved_ip,
              is_online: t.status === 'ONLINE',
              avg_rtt: t.avg_rtt,
              smb_ok: t.smb_port_445,
              winrm_ok: t.winrm_port_5985,
              specs: t.specs,
            });
            if (t.status === 'ONLINE') {
              setDiagStatus({
                ping: t.ping_ok ? 'ok' : 'fail',
                smb: t.smb_port_445 ? 'ok' : 'fail',
                winrm: t.winrm_port_5985 ? 'ok' : 'fail',
              });
            } else if (t.status === 'OFFLINE') {
              setDiagStatus({ ping: 'fail', smb: 'fail', winrm: 'fail' });
            }
          }
        }
      })
      .catch((err) => {
        if (!cancelled) console.warn('Не удалось загрузить подробности заявки:', err);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetails(false);
      });

    return () => {
      cancelled = true;
    };
  }, [rawId]);

  useEffect(() => {
    fetchTemplatesCatalog()
      .then((res) => {
        if (res && Array.isArray(res.templates)) setTemplates(res.templates);
      })
      .catch(() => {});
  }, []);

  const commentsList: any[] = useMemo(() => {
    let raw: any[] = [];
    const rawComments = safeDetails?.comments;
    if (Array.isArray(rawComments)) raw = rawComments;
    else if (
      rawComments &&
      typeof rawComments === 'object' &&
      Array.isArray((rawComments as any).TaskLifetimes)
    )
      raw = (rawComments as any).TaskLifetimes;
    else if (Array.isArray((details as any)?.history)) raw = (details as any).history;
    else if (
      (details as any)?.history &&
      typeof (details as any).history === 'object' &&
      Array.isArray((details as any).history.TaskLifetimes)
    )
      raw = (details as any).history.TaskLifetimes;
    return filterMeaningfulComments(raw);
  }, [safeDetails, details]);

  const rawAttachments = safeDetails?.attachments ?? ticket.attachments;
  const attachmentsList: any[] = useMemo(() => {
    if (Array.isArray(rawAttachments)) return rawAttachments;
    if (
      rawAttachments &&
      typeof rawAttachments === 'object' &&
      Array.isArray((rawAttachments as any).Attachment)
    )
      return (rawAttachments as any).Attachment;
    if (
      rawAttachments &&
      typeof rawAttachments === 'object' &&
      Array.isArray((rawAttachments as any).Attachments)
    )
      return (rawAttachments as any).Attachments;
    return [];
  }, [rawAttachments]);

  const handleGenerateAiSummary = async () => {
    if (!rawId || loadingAiSummary) return;
    setLoadingAiSummary(true);
    try {
      const res = await fetchTicketSummary(
        rawId,
        ticket.title,
        ticket.description || details?.description || '',
        commentsList,
        false
      );
      setAiSummary(res);
      onToast({ type: 'success', message: 'Сводка переписки успешно сформирована AI Hub' });
    } catch (err: any) {
      console.error('Ошибка суммаризации переписки:', err);
      onToast({ type: 'error', message: 'Не удалось сгенерировать AI-сводку переписки' });
    } finally {
      setLoadingAiSummary(false);
    }
  };

  // Network & hardware diagnostic runner
  const runDiag = async (targetHost?: string) => {
    const hostToTest = targetHost || effectiveHost;
    if (!hostToTest) {
      onToast({ type: 'warning', message: 'Имя ПК/хоста не указано в заявке' });
      return;
    }

    setDiagStatus({ ping: 'checking', smb: 'checking', winrm: 'checking' });
    setLoadingDiag(true);
    try {
      const res = await fetchDiagnostics(hostToTest, true);
      setHostDiagnostics(res);
      setDiagStatus({
        ping: res.is_online ? 'ok' : 'fail',
        smb: res.smb_ok ? 'ok' : 'fail',
        winrm: res.winrm_ok ? 'ok' : 'fail',
      });

      onToast({
        type: 'info',
        message:
          res.hosts && res.hosts.length > 1
            ? `Диагностика (${res.hosts.length} ПК): ${
                res.is_online ? 'Есть доступные ПК' : 'Все офлайн'
              }`
            : `Диагностика ${hostToTest}: ${res.is_online ? 'В сети' : 'Недоступен'}`,
      });
    } catch {
      setDiagStatus({ ping: 'fail', smb: 'fail', winrm: 'fail' });
      onToast({ type: 'error', message: `Ошибка диагностики хоста ${hostToTest}` });
    } finally {
      setLoadingDiag(false);
    }
  };

  // Единое состояние и логика принятия решений
  const decisionState = useUnifiedDecision({
    ticket,
    details: safeDetails,
    rawId,
    onUpdateTicket,
    onToast,
    onClose,
    onRefreshDetails: loadDetails,
  });

  // Быстрые сниппеты для подстановки в ответ
  const snippets = useMemo(() => {
    return [];
  }, []);

  const panelClass = expanded
    ? 'fixed inset-0 z-50 bg-neutral-50 dark:bg-neutral-950 flex flex-col overflow-hidden animate-in fade-in duration-200'
    : 'fixed inset-y-0 right-0 z-50 bg-neutral-50 dark:bg-neutral-950 border-l border-neutral-200 dark:border-neutral-800 flex flex-col shadow-2xl animate-in slide-in-from-right duration-200';

  return (
    <div
      className={panelClass}
      style={expanded ? undefined : { width: `${inspectorWidth}px`, maxWidth: '94vw' }}
    >
      {/* Draggable resize handle on left border */}
      {!expanded && (
        <div
          onMouseDown={(e) => {
            e.preventDefault();
            setIsResizing(true);
          }}
          className={`absolute -left-1.5 top-0 bottom-0 w-3 cursor-col-resize z-30 transition-colors group flex items-center justify-center ${
            isResizing ? 'bg-blue-500/20' : 'hover:bg-blue-500/20'
          }`}
          title="Потяните для изменения ширины панели (дважды кликните для сброса к 600px)"
          onDoubleClick={() => {
            setInspectorWidth(600);
            localStorage.setItem('intralink_inspector_width', '600');
          }}
        >
          <div className="w-0.5 h-12 rounded-full bg-neutral-300 dark:bg-neutral-700 group-hover:bg-blue-500 transition-colors" />
        </div>
      )}

      {/* 1. Header */}
      <InspectorHeader
        rawId={rawId}
        ticket={ticket}
        expanded={expanded}
        onToggleExpanded={toggleExpanded}
        onClose={onClose}
        onToast={onToast}
      />

      {/* 2. Body Content & Sticky Action Dock */}
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        {expanded ? (
          <div className="flex-1 min-h-0 max-w-7xl mx-auto w-full p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 overflow-hidden">
            {/* Левая панель: Контекст, Рабочая станция, Вложения, Переписка */}
            <div className="space-y-3.5 overflow-y-auto pr-1">
              <TicketContextSummary
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                attachmentsCount={attachmentsList.length}
                onToast={onToast}
              />
              <InspectorWorkspace
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                hostList={hostList}
                diagStatus={diagStatus}
                hostDiagnostics={hostDiagnostics}
                loadingDiagnostics={loadingDiag}
                onRunDiag={runDiag}
                onToast={onToast}
                onRefreshDetails={loadDetails}
              />
              <AttachmentsSection attachments={attachmentsList} rawId={rawId} />
              <CommentsTimeline
                commentsList={commentsList}
                loadingDetails={loadingDetails}
                isCommentsExpanded={isCommentsExpanded}
                onToggleCommentsExpanded={() => setIsCommentsExpanded((prev) => !prev)}
                expandedMode={true}
                aiSummary={aiSummary}
                loadingAiSummary={loadingAiSummary}
                onGenerateAiSummary={handleGenerateAiSummary}
              />
            </div>

            {/* Правая панель: Доказательная база сценария (FactBag, Регламент, RAG, Аудит) */}
            <div className="space-y-3.5 overflow-y-auto pr-1">
              <UnifiedDecisionPanel
                key={rawId}
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                loadingDetails={loadingDetails}
                decisionState={decisionState}
                onToast={onToast}
              />
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            <div className="p-4 space-y-3.5">
              <TicketContextSummary
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                attachmentsCount={attachmentsList.length}
                onToast={onToast}
              />

              <InspectorWorkspace
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                hostList={hostList}
                diagStatus={diagStatus}
                hostDiagnostics={hostDiagnostics}
                loadingDiagnostics={loadingDiag}
                onRunDiag={runDiag}
                onToast={onToast}
                onRefreshDetails={loadDetails}
              />

              <AttachmentsSection attachments={attachmentsList} rawId={rawId} />

              <UnifiedDecisionPanel
                key={rawId}
                ticket={ticket}
                details={safeDetails}
                rawId={rawId}
                loadingDetails={loadingDetails}
                decisionState={decisionState}
                onToast={onToast}
              />

              <CommentsTimeline
                commentsList={commentsList}
                loadingDetails={loadingDetails}
                isCommentsExpanded={isCommentsExpanded}
                onToggleCommentsExpanded={() => setIsCommentsExpanded((prev) => !prev)}
                expandedMode={false}
                aiSummary={aiSummary}
                loadingAiSummary={loadingAiSummary}
                onGenerateAiSummary={handleGenerateAiSummary}
              />
            </div>
          </div>
        )}

        {/* Прикрепленный Action Dock внизу панели (всегда доступен, единый экземпляр) */}
        <div className={expanded ? 'max-w-7xl mx-auto w-full shrink-0' : 'shrink-0'}>
          <UnifiedActionDock
            replyText={decisionState.replyText}
            setReplyText={decisionState.setReplyText}
            replyMode={decisionState.replyMode}
            setReplyMode={decisionState.setReplyMode}
            expenses={decisionState.expenses}
            setExpenses={decisionState.setExpenses}
            selectedTemplateKey={decisionState.selectedTemplateKey}
            setSelectedTemplateKey={decisionState.setSelectedTemplateKey}
            templates={templates}
            selectedStatusOverride={decisionState.selectedStatusOverride}
            setSelectedStatusOverride={decisionState.setSelectedStatusOverride}
            primaryActionLabel={decisionState.primaryActionLabel}
            targetStatusId={decisionState.targetStatusId}
            targetStatusName={decisionState.targetStatusName}
            actionUnavailable={decisionState.actionUnavailable}
            submitting={decisionState.submitting}
            requiresComment={decisionState.requiresComment}
            commentMissing={decisionState.commentMissing}
            pendingCommand={decisionState.pendingCommand}
            onApplyDecision={decisionState.handleApplyDecision}
            onCancelTicket={decisionState.handleCancelTicket}
            onTakeTicket={decisionState.handleTakeTicket}
            onReanalyze={decisionState.handleReanalyze}
            reanalyzing={decisionState.reanalyzing}
            pendingNewAiDraft={decisionState.pendingNewAiDraft}
            onApplyNewDraft={decisionState.applyNewDraft}
            onDismissNewDraft={decisionState.dismissNewDraft}
            originalAiDraft={decisionState.originalAiDraft}
            isAiDraftApplied={decisionState.isAiDraftApplied}
            isAiDraftEdited={decisionState.isAiDraftEdited}
            recommendedStatusId={decisionState.recommendedStatusId}
            recommendedStatusName={decisionState.recommendedStatusName}
            recommendedExpenses={decisionState.recommendedExpenses}
            onRestoreAiDraft={decisionState.handleRestoreAiDraft}
            onAppendAiDraft={decisionState.handleAppendAiDraft}
            snippets={snippets}
            insertSnippet={decisionState.insertSnippet}
          />
        </div>
      </div>
    </div>
  );
}
