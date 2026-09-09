import {
  IconCpu,
  IconHardDrive,
  IconClock,
  IconAlertTriangle,
  IconAlertCircle,
  IconRefresh,
  IconUser,
  IconServer,
} from '../Icons';
import type { HardwareSpecs } from '../../lib/types';

interface HardwareSpecsCardProps {
  specs: HardwareSpecs | null | undefined;
  loading?: boolean;
  onRefresh?: () => void;
  className?: string;
}

export default function HardwareSpecsCard({
  specs,
  loading = false,
  onRefresh,
  className = '',
}: HardwareSpecsCardProps) {
  if (loading) {
    return (
      <div className={`rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3.5 dark:border-neutral-800 dark:bg-neutral-950/40 animate-pulse ${className}`}>
        <div className="flex items-center justify-between mb-3">
          <div className="h-3.5 w-32 bg-neutral-200 dark:bg-neutral-800 rounded" />
          <div className="h-3 w-16 bg-neutral-200 dark:bg-neutral-800 rounded" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-16 rounded-lg bg-neutral-200/70 dark:bg-neutral-800/60" />
          ))}
        </div>
      </div>
    );
  }

  if (!specs) {
    return (
      <div className={`rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3 text-center dark:border-neutral-800 dark:bg-neutral-950/40 ${className}`}>
        <div className="flex items-center justify-center gap-2 text-xs text-neutral-500 dark:text-neutral-400">
          <IconCpu size={14} className="text-neutral-400" />
          <span>Паспорт оборудования недоступен (WinRM не отвечает или опрос пропущен)</span>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="ml-2 inline-flex items-center gap-1 text-[11px] font-medium text-blue-600 dark:text-blue-400 hover:underline cursor-pointer"
            >
              <IconRefresh size={11} />
              Опросить
            </button>
          )}
        </div>
      </div>
    );
  }

  // RAM status calculations
  const ramUsed = specs.ram_used_percent ?? 0;
  const ramColor = ramUsed > 90 ? 'bg-rose-500' : ramUsed > 75 ? 'bg-amber-500' : 'bg-emerald-500';

  // Disk calculations
  const diskFreePct = specs.disk_c_free_percent ?? 0;
  const diskFreeGb = specs.disk_c_free_gb ?? 0;
  const isDiskLow = specs.flag_disk_low || diskFreeGb < 10 || diskFreePct < 10;
  const isHdd = specs.flag_hdd_bottleneck || specs.disk_type === 'HDD';

  // Uptime formatting
  const uptimeDays = specs.uptime_days ?? 0;
  const uptimeHours = specs.uptime_hours ?? 0;
  const isUptimeCritical = specs.flag_uptime_critical || uptimeDays >= 30;
  const isUptimeWarning = (specs.flag_uptime_warning || uptimeDays >= 14) && !isUptimeCritical;

  const formatBootDate = (isoStr?: string | null) => {
    if (!isoStr) return null;
    try {
      const d = new Date(isoStr);
      return d.toLocaleDateString('ru-RU', {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return isoStr;
    }
  };

  const bootDateFormatted = formatBootDate(specs.boot_time_iso);

  return (
    <div className={`rounded-xl border border-neutral-200/80 bg-neutral-50/50 p-3 space-y-3 dark:border-neutral-800 dark:bg-neutral-950/40 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-neutral-200/70 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            <IconCpu size={13} />
          </span>
          <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-700 dark:text-neutral-300">
            Паспорт оборудования & Uptime
          </span>
          {specs.manufacturer && specs.model && (
            <span className="hidden sm:inline text-[11px] text-neutral-500 dark:text-neutral-400 font-mono truncate max-w-[200px]" title={`${specs.manufacturer} ${specs.model}`}>
              {specs.manufacturer} {specs.model}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              className="inline-flex items-center gap-1 rounded-md border border-neutral-200 bg-white px-2 py-0.5 text-[10.5px] font-semibold text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:bg-neutral-800 cursor-pointer"
              title="Обновить системные характеристики ПК"
            >
              <IconRefresh size={11} />
              <span>Обновить</span>
            </button>
          )}
        </div>
      </div>

      {/* Critical Alert Banners */}
      {isUptimeCritical && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50/90 p-2 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <IconAlertCircle size={15} className="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" />
          <div className="leading-tight">
            <span className="font-bold">Критический аптайм ({uptimeDays} дн.):</span> ПК работает без перезагрузки больше месяца. Высокий риск утечек памяти, зависаний и системных сбоев.
          </div>
        </div>
      )}

      {isUptimeWarning && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 p-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300">
          <IconAlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="leading-tight">
            <span className="font-bold">Предупреждение аптайма ({uptimeDays} дн.):</span> Компьютер не перезагружался более двух недель. Возможны замедления в 1С и браузере.
          </div>
        </div>
      )}

      {isDiskLow && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50/90 p-2 text-xs text-rose-800 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-300">
          <IconAlertTriangle size={15} className="mt-0.5 shrink-0 text-rose-600 dark:text-rose-400" />
          <div className="leading-tight">
            <span className="font-bold">Мало места на C: ({diskFreeGb} ГБ / {diskFreePct}%):</span> Диск почти заполнен. Рекомендуется очистка временных файлов и кэша.
          </div>
        </div>
      )}

      {isHdd && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50/90 p-2 text-xs text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-300">
          <IconAlertTriangle size={15} className="mt-0.5 shrink-0 text-amber-600 dark:text-amber-400" />
          <div className="leading-tight">
            <span className="font-bold">Установлен медленный диск (HDD):</span> Механический накопитель является узким местом при запуске программ и загрузке Windows.
          </div>
        </div>
      )}

      {/* Grid of Key Specs (Auto-fit responsive) */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {/* 1. CPU */}
        <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex items-center justify-between gap-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400">
            <span>Процессор</span>
            <IconCpu size={12} />
          </div>
          <div className="mt-1 font-semibold text-xs text-neutral-900 dark:text-neutral-100 truncate" title={specs.cpu_name || 'Не определен'}>
            {specs.cpu_name || '—'}
          </div>
          <div className="mt-0.5 text-[10px] font-mono text-neutral-500 dark:text-neutral-400">
            {specs.cpu_cores ? `${specs.cpu_cores} ядра / ${specs.cpu_threads || specs.cpu_cores} пот.` : '—'}
          </div>
        </div>

        {/* 2. RAM */}
        <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex items-center justify-between gap-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400">
            <span>Память (RAM)</span>
            <span className="font-mono text-[10px] text-neutral-500">{ramUsed}%</span>
          </div>
          <div className="mt-1 font-semibold text-xs text-neutral-900 dark:text-neutral-100 flex items-center justify-between">
            <span>{specs.total_ram_gb ? `${specs.total_ram_gb} ГБ` : '—'}</span>
            <span className="text-[10px] font-normal text-neutral-400">
              своб. {specs.free_ram_gb ?? '—'} ГБ
            </span>
          </div>
          {/* Progress bar */}
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
            <div className={`h-full ${ramColor} transition-all duration-300`} style={{ width: `${Math.min(100, ramUsed)}%` }} />
          </div>
        </div>

        {/* 3. Disk C: */}
        <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex items-center justify-between gap-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400">
            <span>Диск C:</span>
            <span className={`px-1 py-0.2 rounded text-[9px] font-mono font-bold ${
              isHdd
                ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
            }`}>
              {specs.disk_type || 'SSD'}
            </span>
          </div>
          <div className="mt-1 font-semibold text-xs text-neutral-900 dark:text-neutral-100">
            {specs.disk_c_free_gb !== null && specs.disk_c_free_gb !== undefined ? `${specs.disk_c_free_gb} ГБ` : '—'}
            <span className="text-[10px] font-normal text-neutral-400 ml-1">
              из {specs.disk_c_total_gb ?? '—'} ГБ
            </span>
          </div>
          <div className="mt-0.5 text-[10px] font-mono text-neutral-500">
            свободно {diskFreePct}%
          </div>
        </div>

        {/* 4. Uptime & OS */}
        <div className="rounded-lg border border-neutral-200/70 bg-white p-2.5 shadow-2xs dark:border-neutral-800 dark:bg-neutral-900">
          <div className="flex items-center justify-between gap-1 text-[10px] font-bold uppercase tracking-wider text-neutral-400">
            <span>Аптайм</span>
            <IconClock size={12} className={isUptimeCritical ? 'text-rose-500' : isUptimeWarning ? 'text-amber-500' : 'text-emerald-500'} />
          </div>
          <div className={`mt-1 font-mono font-bold text-xs ${
            isUptimeCritical ? 'text-rose-600 dark:text-rose-400' : isUptimeWarning ? 'text-amber-600 dark:text-amber-400' : 'text-neutral-900 dark:text-neutral-100'
          }`}>
            {uptimeDays} дн. {uptimeHours} ч.
          </div>
          <div className="mt-0.5 text-[10px] font-mono text-neutral-400 truncate" title={bootDateFormatted ? `Загрузка: ${bootDateFormatted}` : undefined}>
            {bootDateFormatted ? `с ${bootDateFormatted}` : specs.os_caption || 'Windows'}
          </div>
        </div>
      </div>

      {/* Meta Bar: Active User, Model, OS Architecture */}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-neutral-200/60 dark:border-neutral-800/60 text-[10.5px] text-neutral-500 dark:text-neutral-400">
        <div className="flex items-center gap-3">
          {specs.logged_in_user && (
            <div className="flex items-center gap-1">
              <IconUser size={12} />
              <span className="font-mono font-medium text-neutral-700 dark:text-neutral-300">
                {specs.logged_in_user}
              </span>
            </div>
          )}
          {specs.os_caption && (
            <div className="flex items-center gap-1">
              <IconServer size={12} />
              <span className="truncate max-w-[220px]" title={specs.os_caption}>
                {specs.os_caption} {specs.os_arch ? `(${specs.os_arch})` : ''}
              </span>
            </div>
          )}
        </div>

        {specs.pc_name && (
          <div className="font-mono text-[10px] text-neutral-400">
            ID: {specs.pc_name}
          </div>
        )}
      </div>
    </div>
  );
}
