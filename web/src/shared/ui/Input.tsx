import React from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  icon?: React.ReactNode;
  label?: string;
  error?: string;
}

export const Input: React.FC<InputProps> = ({
  icon,
  label,
  error,
  className = "",
  ...props
}) => {
  return (
    <div className="w-full space-y-1">
      {label && (
        <label className="block text-[11px] font-medium text-slate-300">
          {label}
        </label>
      )}
      <div className="relative flex items-center">
        {icon && (
          <span className="absolute left-2.5 text-slate-400 pointer-events-none shrink-0">
            {icon}
          </span>
        )}
        <input
          className={`w-full bg-[#141822] border border-[#252b3b] rounded-md px-3 py-1.5 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 transition-colors disabled:opacity-50 disabled:bg-[#0f121a] ${
            icon ? "pl-8" : ""
          } ${error ? "border-red-500/80 focus:ring-red-500" : ""} ${className}`}
          {...props}
        />
      </div>
      {error && <p className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
};

export interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

export const Textarea: React.FC<TextareaProps> = ({
  label,
  error,
  className = "",
  rows = 3,
  ...props
}) => {
  return (
    <div className="w-full space-y-1">
      {label && (
        <label className="block text-[11px] font-medium text-slate-300">
          {label}
        </label>
      )}
      <textarea
        rows={rows}
        className={`w-full bg-[#141822] border border-[#252b3b] rounded-md px-3 py-2 text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500 transition-colors disabled:opacity-50 disabled:bg-[#0f121a] resize-none ${
          error ? "border-red-500/80 focus:ring-red-500" : ""
        } ${className}`}
        {...props}
      />
      {error && <p className="text-[11px] text-red-400">{error}</p>}
    </div>
  );
};
