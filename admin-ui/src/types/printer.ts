export interface PrinterModel {
  model_key: string;
  display_name: string;
  vendor: string;
  supported_connections?: string[];
  inf_name?: string;
  driver_name?: string;
}

export interface PrinterKnowledgeBase {
  printers: Record<string, PrinterModel>;
  vendors?: string[];
  universal_drivers?: Record<string, any>;
}

export interface PrintJob {
  task_id: number;
  tg_user_id?: number;
  target_pc: string;
  model_key: string;
  connection_type: 'tcpip' | 'usb';
  printer_address?: string;
  state: 'pending' | 'probing' | 'installing' | 'done' | 'failed' | 'cancelled' | 'waiting_approval';
  error_message?: string;
  created_at?: string | number;
  updated_at?: string | number;
  logs?: string[];
  is_manual?: boolean;
  driver_info?: Record<string, any>;
}

export interface IndexerStatus {
  is_running: boolean;
  last_run: number | null;
  last_result: {
    status?: string;
    total_drivers?: number;
    extracted_count?: number;
    errors?: string[];
  } | null;
}

export interface ManualJobPayload {
  target_pc: string;
  model_key: string;
  connection_type: 'tcpip' | 'usb';
  printer_address?: string;
}
