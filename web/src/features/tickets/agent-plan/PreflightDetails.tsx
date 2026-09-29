import React from "react";
import { GitBranch, KeyRound, ShieldCheck, UserRound } from "lucide-react";

import { detailText, type PreflightItem } from "./presentation";

export function PreflightDetails({ item }: { item: PreflightItem }) {
  const targetOu = detailText(item.details, "target_ou");
  const login = detailText(item.details, "preview_sam_account_name");
  const upn = detailText(item.details, "preview_upn");
  const method = detailText(item.details, "ou_resolution_method");
  const candidates = Array.isArray(item.details.ou_candidates)
    ? item.details.ou_candidates.filter((candidate): candidate is Record<string, unknown> => Boolean(candidate && typeof candidate === "object"))
    : [];

  return (
    <div className="mt-2 space-y-2">
      <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(210px,1fr))]">
        {targetOu && <PreflightValue icon={<GitBranch />} label="Целевой OU" value={targetOu} />}
        {login && <PreflightValue icon={<UserRound />} label="Предварительный логин" value={login} mono />}
        {upn && <PreflightValue icon={<KeyRound />} label="UPN" value={upn} mono />}
        {method && <PreflightValue icon={<ShieldCheck />} label="Метод выбора OU" value={method} mono />}
      </div>
      {candidates.length > 0 && !targetOu && (
        <div className="rounded border border-amber-800/40 bg-amber-950/10 p-2.5">
          <p className="text-[10px] font-medium uppercase tracking-wider text-amber-300/80">Кандидаты OU</p>
          <div className="mt-1.5 space-y-1 text-[10px] text-neutral-400">
            {candidates.slice(0, 5).map((candidate, index) => (
              <div key={`${String(candidate.distinguished_name)}-${index}`} className="flex justify-between gap-3">
                <span className="break-all">{String(candidate.distinguished_name || candidate.name || "Неизвестный OU")}</span>
                <span className="shrink-0 font-mono">{Math.round(Number(candidate.score || 0) * 100)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="text-[10px] text-neutral-500">
        Действительна до {new Date(item.expires_at).toLocaleTimeString("ru-RU")}
        {item.checks.length ? ` · ${item.checks.length} проверок` : ""}
      </p>
    </div>
  );
}

function PreflightValue({
  icon,
  label,
  value,
  mono,
}: {
  icon: React.ReactElement<{ className?: string; strokeWidth?: number }>;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded border border-neutral-800 bg-[#101319] px-2.5 py-2">
      <div className="flex items-center gap-1.5 text-[10px] text-neutral-500">
        {React.cloneElement(icon, { className: "h-3 w-3", strokeWidth: 1.5 })}
        {label}
      </div>
      <p className={`mt-1 break-all text-[11px] text-neutral-200 ${mono ? "font-mono" : ""}`}>{value}</p>
    </div>
  );
}
