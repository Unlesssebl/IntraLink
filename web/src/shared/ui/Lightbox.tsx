import React, { useEffect, useState } from "react";
import { X, ZoomIn, ZoomOut, ExternalLink } from "lucide-react";

export interface LightboxProps {
  src: string | null;
  alt?: string;
  isOpen: boolean;
  onClose: () => void;
}

export const Lightbox: React.FC<LightboxProps> = ({
  src,
  alt = "Screenshot preview",
  isOpen,
  onClose,
}) => {
  const [scale, setScale] = useState(1);

  useEffect(() => {
    if (isOpen) {
      setScale(1);
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") onClose();
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, onClose]);

  if (!isOpen || !src) return null;

  const zoomIn = (e: React.MouseEvent) => {
    e.stopPropagation();
    setScale((prev) => Math.min(prev + 0.25, 3));
  };

  const zoomOut = (e: React.MouseEvent) => {
    e.stopPropagation();
    setScale((prev) => Math.max(prev - 0.25, 0.5));
  };

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/85 backdrop-blur-xs p-4 animate-in fade-in duration-150"
    >
      {/* Top action bar */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="absolute top-4 right-4 flex items-center gap-2 bg-[#121316]/95 border border-neutral-800 px-3 py-1.5 rounded-lg shadow-xl"
      >
        <span className="text-xs text-neutral-300 font-medium mr-2 max-w-xs truncate">{alt}</span>
        <button
          onClick={zoomIn}
          className="p-1.5 text-neutral-300 hover:text-white hover:bg-neutral-800/60 rounded transition-colors"
          title="Увеличить"
        >
          <ZoomIn className="w-4 h-4" />
        </button>
        <button
          onClick={zoomOut}
          className="p-1.5 text-neutral-300 hover:text-white hover:bg-neutral-800/60 rounded transition-colors"
          title="Уменьшить"
        >
          <ZoomOut className="w-4 h-4" />
        </button>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          download
          className="p-1.5 text-neutral-300 hover:text-white hover:bg-neutral-800/60 rounded transition-colors"
          title="Открыть в новой вкладке / Скачать"
        >
          <ExternalLink className="w-4 h-4" />
        </a>
        <button
          onClick={onClose}
          className="p-1.5 text-neutral-300 hover:text-rose-400 hover:bg-neutral-800/60 rounded transition-colors ml-1"
          title="Закрыть (Esc)"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Image container */}
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-w-[90vw] max-h-[85vh] overflow-auto flex items-center justify-center p-2 rounded-lg"
      >
        <img
          src={src}
          alt={alt}
          style={{ transform: `scale(${scale})`, transformOrigin: "center center" }}
          className="rounded shadow-2xl transition-transform duration-150 object-contain max-h-[80vh] max-w-[85vw]"
        />
      </div>
    </div>
  );
};
