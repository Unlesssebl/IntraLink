import React from 'react';
import { IconAlertTriangle, IconClose } from '../Icons';

export interface BulkConfirmModalState {
  open: boolean;
  actionType: 'take' | 'cancel' | 'resolve';
  targetStatusId: number;
  statusLabelName: string;
  count: number;
  hasRepair: boolean;
  ticketIds: number[];
}

interface BulkConfirmModalProps {
  modal: BulkConfirmModalState;
  processingBulk: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export default function BulkConfirmModal({
  modal,
  processingBulk,
  onClose,
  onConfirm,
}: BulkConfirmModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-2xs p-4">
      <div className="w-full max-w-md bg-white dark:bg-neutral-900 rounded-xl shadow-2xl border border-neutral-200 dark:border-neutral-800 p-5 space-y-4 animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-neutral-900 dark:text-neutral-100">
            Подтверждение массового действия
          </h3>
          <button
            onClick={onClose}
            disabled={processingBulk}
            className="text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 cursor-pointer p-1"
          >
            <IconClose size={16} />
          </button>
        </div>

        <p className="text-[14px] text-neutral-700 dark:text-neutral-300 leading-relaxed">
          Вы уверены, что хотите перевести <strong>{modal.count}</strong> {modal.count === 1 ? 'заявку' : 'заявок'} в статус{' '}
          <span className="font-bold underline">«{modal.statusLabelName}»</span>?
        </p>

        {/* List of ticket IDs */}
        <div className="bg-neutral-100 dark:bg-neutral-800/80 p-2.5 rounded-lg text-[12.5px] font-mono text-neutral-700 dark:text-neutral-300 max-h-24 overflow-y-auto">
          {modal.ticketIds.map(id => `#${id}`).join(', ')}
        </div>

        {/* Warning if hardware repair is selected for bulk resolve */}
        {modal.hasRepair && modal.actionType === 'resolve' && (
          <div className="p-3 bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 rounded-lg text-[13px] text-amber-900 dark:text-amber-200 leading-snug flex items-start gap-2">
            <IconAlertTriangle size={16} className="text-amber-600 shrink-0 mt-0.5" />
            <div>
              <strong>Внимание (Verified Execution):</strong> в выборке присутствуют заявки на аппаратный ремонт (Каб. 112). Завершайте их только после физической выдачи устройства заявителю!
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            disabled={processingBulk}
            className="px-4 py-2 text-[13px] font-medium text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg transition-colors cursor-pointer"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={processingBulk}
            className="px-4 py-2 bg-neutral-900 dark:bg-neutral-100 text-white dark:text-neutral-900 text-[13px] font-bold rounded-lg hover:bg-neutral-800 dark:hover:bg-neutral-200 transition-colors cursor-pointer disabled:opacity-50"
          >
            {processingBulk ? 'Выполнение...' : 'Подтвердить'}
          </button>
        </div>
      </div>
    </div>
  );
}
