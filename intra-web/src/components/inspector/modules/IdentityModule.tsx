import { useState } from 'react';
import type { InspectorModuleProps } from './InspectorModuleProps';
import {
  IconUser,
  IconLock,
  IconCheckCircle,
  IconAlertCircle,
  IconCopy,
  IconRefresh,
} from '../../Icons';

export default function IdentityModule({
  ticket,
  details,
  facts,
  onToast,
  onExecuteAction,
}: InspectorModuleProps) {
  const [unlocking, setUnlocking] = useState(false);

  const requesterName = ticket.requesterName || details?.creator || '';
  const requesterLogin = ticket.requesterLogin || facts.login || facts.samaccountname || '';
  const phone = ticket.requesterPhone || details?.phone || facts.phone || '';
  const department = ticket.department || details?.department || facts.department || '';
  const company = ticket.company || details?.company || facts.company || '';
  const room = ticket.room || details?.room || facts.room || '';

  // Required attributes checklist for create_user / grant_wlan
  const requiredFields = [
    { key: 'fio', label: 'ФИО сотрудника', val: requesterName },
    { key: 'company', label: 'Организация', val: company },
    { key: 'department', label: 'Подразделение', val: department },
    { key: 'phone', label: 'Контактный телефон', val: phone },
    { key: 'room', label: 'Кабинет / локация', val: room },
  ];

  const missingCount = requiredFields.filter((f) => !f.val).length;
  const isLockedOut = Boolean(facts.is_locked_out || facts.locked);

  const handleUnlockAccount = async () => {
    if (!requesterLogin) {
      onToast({ type: 'warning', message: 'Логин учетной записи не определен' });
      return;
    }
    setUnlocking(true);
    try {
      if (onExecuteAction) {
        await onExecuteAction('unlock_ad_account', { login: requesterLogin });
      } else {
        await navigator.clipboard.writeText(`Unlock-ADAccount -Identity "${requesterLogin}"`);
        onToast({ type: 'info', message: 'Команда разблокировки УЗ скопирована в буфер' });
      }
    } catch (err: any) {
      onToast({ type: 'error', message: `Ошибка разблокировки: ${err?.message || err}` });
    } finally {
      setUnlocking(false);
    }
  };

  return (
    <div className="space-y-3">
      {/* Employee Identity Card */}
      <div className="rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-blue-50 text-blue-600 dark:bg-blue-950/60 dark:text-blue-400">
              <IconUser size={13} />
            </span>
            <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">
              Карточка сотрудника & AD
            </span>
          </div>

          {requesterLogin && (
            <span className="font-mono text-xs font-semibold px-2 py-0.5 rounded bg-neutral-100 dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200">
              @{requesterLogin}
            </span>
          )}
        </div>

        {/* AD Account Status Banner */}
        <div className="flex items-center justify-between gap-2 p-2.5 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800 text-xs">
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${isLockedOut ? 'bg-rose-500 animate-pulse' : 'bg-emerald-500'}`} />
            <span className="font-medium text-neutral-900 dark:text-neutral-100">
              Статус в Active Directory:
            </span>
            <span className={`font-semibold ${isLockedOut ? 'text-rose-600 dark:text-rose-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
              {isLockedOut ? 'Заблокирована (Locked Out)' : 'Активна'}
            </span>
          </div>

          {isLockedOut && (
            <button
              type="button"
              onClick={handleUnlockAccount}
              disabled={unlocking}
              className="inline-flex items-center gap-1 rounded-md bg-rose-50 border border-rose-200 px-2 py-0.5 text-[11px] font-semibold text-rose-700 hover:bg-rose-100 dark:bg-rose-950/60 dark:border-rose-800 dark:text-rose-300 cursor-pointer"
            >
              <IconLock size={11} />
              <span>{unlocking ? 'Разблокировка...' : 'Разблокировать УЗ'}</span>
            </button>
          )}
        </div>

        {/* Required Attributes Checklist */}
        <div className="space-y-1.5 pt-1">
          <div className="flex items-center justify-between text-[10.5px] uppercase font-bold text-neutral-400 tracking-wider">
            <span>Проверка реквизитов заявки:</span>
            <span>{missingCount === 0 ? 'Все данные заполнены' : `Не хватает: ${missingCount}`}</span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 text-xs">
            {requiredFields.map((f) => (
              <div
                key={f.key}
                className="flex items-center justify-between p-2 rounded-lg bg-white dark:bg-neutral-900 border border-neutral-200/70 dark:border-neutral-800"
              >
                <div className="min-w-0 pr-2">
                  <div className="text-[10px] text-neutral-400">{f.label}</div>
                  <div className="font-semibold text-neutral-900 dark:text-neutral-100 truncate">
                    {f.val || '—'}
                  </div>
                </div>

                <span className={`px-1.5 py-0.5 rounded text-[9.5px] font-mono font-bold shrink-0 ${
                  f.val
                    ? 'bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800'
                    : 'bg-rose-50 text-rose-700 border border-rose-200 dark:bg-rose-950/60 dark:text-rose-300 dark:border-rose-800'
                }`}>
                  {f.val ? 'VALID' : 'MISSING'}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
