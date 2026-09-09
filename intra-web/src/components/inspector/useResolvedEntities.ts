import { useMemo } from 'react';
import type { Ticket } from '../../data/mock';
import type { TaskDetails } from '../../lib/types';

export interface ResolvedTicketEntities {
  host: {
    primaryHost: string;
    hostList: string[];
    hasHost: boolean;
  };
  requester: {
    name: string;
    login: string;
    phone: string;
    department: string;
    company: string;
    room: string;
    locationStr: string;
  };
  printer: {
    name: string;
    ip: string;
    targetHost: string;
    spoolerStatus: string;
    queueCount: number;
    hasPrinter: boolean;
  };
  identity: {
    isLockedOut: boolean;
    requiredFields: Array<{ key: string; label: string; val: string }>;
    missingCount: number;
  };
  routing: {
    currentService: string;
    targetService: string;
    isDowntime: boolean;
    reason: string;
    isRedirect: boolean;
  };
  rag: {
    results: any[];
    hasResults: boolean;
    bestPct: number | null;
  };
  scenarioKey: string;
  facts: Record<string, any>;
}

export function useResolvedEntities(
  ticket: Ticket,
  details: TaskDetails | null,
  rawHostList?: string[]
): ResolvedTicketEntities {
  return useMemo(() => {
    const envelope = details?.decision_envelope;
    const facts = (envelope?.facts || {}) as Record<string, any>;
    const scenarioKey =
      envelope?.rule?.scenario_key ||
      envelope?.rule?.rule_type ||
      envelope?.scenario_key ||
      details?.suggested_action?.scenario_key ||
      details?.suggested_action?.rule_type ||
      ticket.scenarioKey ||
      '';

    // 1. Host resolution
    const effectiveHostRaw = ticket.host || details?.pc_name || facts.pc_name || '';
    const parsedHosts: string[] = effectiveHostRaw
      ? Array.from(
          new Set<string>(
            effectiveHostRaw
              .split(/[,;]+/)
              .map((h: string) => h.trim().replace(/\s+/g, ''))
              .filter(Boolean)
          )
        )
      : [];
    const hostList: string[] = rawHostList && rawHostList.length > 0 ? rawHostList : parsedHosts;
    const primaryHost = hostList[0] || effectiveHostRaw || '';

    // 2. Requester resolution
    const requesterName = ticket.requesterName || details?.creator || facts.creator || '';
    const requesterLogin = ticket.requesterLogin || facts.login || facts.samaccountname || '';
    const phone = ticket.requesterPhone || details?.phone || facts.phone || '';
    const department = ticket.department || details?.department || facts.department || '';
    const company = ticket.company || details?.company || facts.company || '';
    const room = ticket.room || details?.room || facts.room || '';
    const locationStr = [room ? `каб. ${room}` : '', department].filter(Boolean).join(' · ');

    // 3. Printer resolution
    const printerName =
      facts.printer_name || facts.printer || details?.custom_fields?.['printer'] || '';
    const printerIp = facts.printer_ip || facts.target_ip || '';
    const targetHost =
      facts.target_pc || facts.pc_name || primaryHost || details?.pc_name || ticket.host || '';
    const spoolerStatus = facts.spooler_status || facts.spooler || 'Running';
    const queueCount = facts.queue_count ?? facts.pending_jobs ?? 0;
    const hasPrinter =
      Boolean(printerName || printerIp) ||
      (ticket.serviceName || '').toLowerCase().includes('печат') ||
      (ticket.serviceName || '').toLowerCase().includes('оргтехник');

    // 4. Identity & AD resolution
    const isLockedOut = Boolean(facts.is_locked_out || facts.locked);
    const requiredFields = [
      { key: 'fio', label: 'ФИО сотрудника', val: requesterName },
      { key: 'company', label: 'Организация', val: company },
      { key: 'department', label: 'Подразделение', val: department },
      { key: 'phone', label: 'Контактный телефон', val: phone },
      { key: 'room', label: 'Кабинет / локация', val: room },
    ];
    const missingCount = requiredFields.filter((f) => !f.val).length;

    // 5. Routing & Redirect resolution
    const currentService = ticket.serviceName || 'Текущий сервис';
    const targetService =
      facts.target_service_name ||
      envelope?.rule?.target_service ||
      details?.suggested_action?.target_service ||
      'Целевой сервис каталога';
    const isDowntime = Boolean(facts.is_downtime || facts.production_impact);
    const reason =
      facts.redirect_reason ||
      details?.suggested_action?.reason ||
      'Тематика обращения относится к компетенции другой группы поддержки.';
    const isRedirect =
      scenarioKey.toLowerCase().includes('redirect') ||
      envelope?.rule?.rule_type === 'redirect' ||
      details?.suggested_action?.rule_type === 'service_redirect';

    // 6. RAG resolution
    const ragResults = details?.rag_results || details?.kb_matches || [];
    const bestPct =
      ragResults.length > 0
        ? Math.round(
            ragResults[0].similarity_pct ||
              (ragResults[0].similarity ? ragResults[0].similarity * 100 : 0) ||
              (1 - (ragResults[0].distance || 0.3)) * 100
          )
        : null;

    return {
      host: {
        primaryHost,
        hostList,
        hasHost: hostList.length > 0,
      },
      requester: {
        name: requesterName,
        login: requesterLogin,
        phone,
        department,
        company,
        room,
        locationStr,
      },
      printer: {
        name: printerName,
        ip: printerIp,
        targetHost,
        spoolerStatus,
        queueCount,
        hasPrinter,
      },
      identity: {
        isLockedOut,
        requiredFields,
        missingCount,
      },
      routing: {
        currentService,
        targetService,
        isDowntime,
        reason,
        isRedirect,
      },
      rag: {
        results: ragResults,
        hasResults: ragResults.length > 0,
        bestPct,
      },
      scenarioKey,
      facts,
    };
  }, [ticket, details, rawHostList]);
}
