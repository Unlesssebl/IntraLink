import React, { useState } from 'react';
import { IconPaperclip, IconClose } from '../Icons';

interface AttachmentsSectionProps {
  attachments: any[];
  rawId: number;
}

export default function AttachmentsSection({ attachments, rawId }: AttachmentsSectionProps) {
  const [previewModalImg, setPreviewModalImg] = useState<{ url: string; name: string } | null>(null);
  const [imgZoom, setImgZoom] = useState(false);

  if (!attachments || attachments.length === 0) return null;

  return (
    <>
      <div className="border border-neutral-200 dark:border-neutral-800 rounded-xl p-3 bg-white dark:bg-neutral-900 shadow-xs space-y-2">
        <div className="flex items-center justify-between text-[11px] font-bold uppercase tracking-wider text-neutral-400">
          <div className="flex items-center gap-1.5">
            <IconPaperclip size={13} />
            <span>Вложения ({attachments.length})</span>
          </div>
          <span className="text-[10px] font-normal lowercase text-neutral-400">клик для превью</span>
        </div>

        <div className="flex flex-wrap gap-2">
          {attachments.map((att: any, idx: number) => {
            const isImg = /\.(png|jpe?g|bmp|webp|gif)$/i.test(att.name || att.FileName || '');
            const attId = att.id || att.Id || idx + 1;
            const attName = att.name || att.FileName || `Вложение ${idx + 1}`;
            const downloadUrl = `/api/v1/tasks/${rawId}/attachments/${attId}?name=${encodeURIComponent(attName)}`;

            return (
              <button
                key={`${attId}-${idx}`}
                type="button"
                onClick={() => {
                  if (isImg) {
                    setImgZoom(false);
                    setPreviewModalImg({ url: downloadUrl, name: attName });
                  } else {
                    window.open(downloadUrl, '_blank');
                  }
                }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-neutral-200 dark:border-neutral-750 bg-neutral-50 dark:bg-neutral-800/80 hover:bg-neutral-100 dark:hover:bg-neutral-800 hover:border-neutral-300 dark:hover:border-neutral-600 text-[12px] font-medium text-neutral-700 dark:text-neutral-200 transition-colors cursor-pointer group max-w-full"
                title={isImg ? `Просмотреть скриншот: ${attName}` : `Открыть файл: ${attName}`}
              >
                {isImg ? (
                  <svg className="w-3.5 h-3.5 text-blue-500 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <polyline points="21 15 16 10 5 21" />
                  </svg>
                ) : (
                  <IconPaperclip size={13} className="text-neutral-400 group-hover:text-blue-500 shrink-0" />
                )}
                <span className="truncate max-w-[220px]">{attName}</span>
                {att.size ? (
                  <span className="text-[10.5px] text-neutral-400 font-mono shrink-0">
                    {Math.round(att.size / 1024)} КБ
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>

      {/* Lightbox Modal for Image Preview */}
      {previewModalImg && (
        <div
          className="fixed inset-0 z-50 bg-black/85 backdrop-blur-xs flex flex-col items-center justify-center p-4 animate-in fade-in duration-150"
          onClick={() => setPreviewModalImg(null)}
        >
          {/* Top Bar */}
          <div
            className="w-full max-w-4xl flex items-center justify-between pb-3 text-white px-2"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 truncate">
              <IconPaperclip size={15} className="text-neutral-400" />
              <span className="font-mono text-sm font-semibold truncate">{previewModalImg.name}</span>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <button
                type="button"
                onClick={() => setImgZoom(z => !z)}
                className="px-3 py-1 text-xs rounded bg-white/10 hover:bg-white/20 transition-colors cursor-pointer"
              >
                {imgZoom ? '1x' : '1.5x Zoom'}
              </button>
              <a
                href={previewModalImg.url}
                download={previewModalImg.name}
                className="px-3.5 py-1 text-xs font-semibold rounded bg-neutral-100 text-neutral-900 hover:bg-neutral-200 transition-colors cursor-pointer"
              >
                Скачать
              </a>
              <button
                type="button"
                onClick={() => setPreviewModalImg(null)}
                className="p-1 rounded-full hover:bg-white/20 text-neutral-300 hover:text-white transition-colors cursor-pointer"
                title="Закрыть (Esc)"
              >
                <IconClose size={18} />
              </button>
            </div>
          </div>

          {/* Image Canvas */}
          <div
            className="max-w-4xl max-h-[82vh] overflow-auto flex items-center justify-center rounded-xl bg-neutral-900/60 p-2 border border-white/10"
            onClick={(e) => e.stopPropagation()}
          >
            <img
              src={previewModalImg.url}
              alt={previewModalImg.name}
              className={`rounded-lg object-contain transition-all duration-200 cursor-zoom-in ${
                imgZoom ? 'max-w-none scale-150' : 'max-w-full max-h-[78vh]'
              }`}
              onClick={() => setImgZoom(z => !z)}
            />
          </div>
        </div>
      )}
    </>
  );
}
