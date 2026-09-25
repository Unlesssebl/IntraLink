import React from "react";
import { Database, Copy, Check } from "lucide-react";
import { TicketDetail } from "@/shared/api";
import { useToast } from "@/shared/ui";

export interface DomainMetadataGridProps {
  ticket: TicketDetail;
}

export const DomainMetadataGrid: React.FC<DomainMetadataGridProps> = ({ ticket }) => {
  const toast = useToast();
  const [copiedKey, setCopiedKey] = React.useState<string | null>(null);

  // Collect relevant domain metadata fields
  const fields: { label: string; value: string; key: string }[] = [];

  if (ticket.service_name) {
    fields.push({ label: "Сервис", value: ticket.service_name, key: "service" });
  }

  if (ticket.priority_name) {
    fields.push({ label: "Приоритет", value: ticket.priority_name, key: "priority" });
  }

  if (ticket.pc_name) {
    fields.push({ label: "Рабочая станция", value: ticket.pc_name, key: "pc_name" });
  }

  // Entities if parsed by backend
  if (ticket.entities) {
    if (ticket.entities.org_name) {
      fields.push({ label: "Организация", value: ticket.entities.org_name, key: "org" });
    }
    if (ticket.entities.department) {
      fields.push({ label: "Подразделение", value: ticket.entities.department, key: "dept" });
    }
    if (ticket.entities.ip_address) {
      fields.push({ label: "IP адрес", value: ticket.entities.ip_address, key: "ip" });
    }
    if (ticket.entities.directum_task_type) {
      fields.push({ label: "Тип задачи Directum", value: ticket.entities.directum_task_type, key: "directum_type" });
    }
  }

  // Custom fields if present
  if (ticket.custom_fields && typeof ticket.custom_fields === "object") {
    for (const [k, v] of Object.entries(ticket.custom_fields)) {
      if (v !== null && v !== undefined && v !== "") {
        fields.push({ label: k, value: String(v), key: `cf_${k}` });
      }
    }
  }

  if (fields.length === 0) return null;

  const handleCopy = (key: string, val: string) => {
    navigator.clipboard.writeText(val);
    setCopiedKey(key);
    toast.success(`Скопировано: ${val}`);
    setTimeout(() => setCopiedKey(null), 1500);
  };

  return (
    <div className="p-3 bg-[#111317] border border-neutral-800/80 rounded-lg space-y-2 text-xs">
      <div className="text-[10px] uppercase font-semibold text-neutral-400 tracking-wider flex items-center gap-1.5">
        <Database className="w-3.5 h-3.5 text-neutral-500" />
        Доменные атрибуты и метаданные
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 pt-1">
        {fields.map((f) => (
          <div
            key={f.key}
            onClick={() => handleCopy(f.key, f.value)}
            className="p-1.5 bg-[#14171d] border border-neutral-800 rounded hover:border-neutral-700 transition-colors cursor-pointer group flex flex-col justify-between"
            title="Нажмите, чтобы скопировать"
          >
            <span className="text-[10px] text-neutral-500 truncate">{f.label}</span>
            <div className="flex items-center justify-between gap-1 mt-0.5">
              <span className="font-mono text-neutral-200 truncate group-hover:text-white font-medium text-[11px]">
                {f.value}
              </span>
              {copiedKey === f.key ? (
                <Check className="w-3 h-3 text-emerald-400 shrink-0" />
              ) : (
                <Copy className="w-3 h-3 text-neutral-600 group-hover:text-neutral-400 shrink-0" />
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
