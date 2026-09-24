import { useQuery, useMutation, useQueryClient, QueryClient } from "@tanstack/react-query";
import {
  ticketsApi,
  TicketListItem,
  TicketDetail,
  TicketLifetimeEvent,
} from "@/shared/api";
import { getStatusMeta } from "@/shared/statuses";

// -------------------------------------------------------------
// Query Key Factory (Strict Typing)
// -------------------------------------------------------------
export const ticketKeys = {
  all: ["tickets"] as const,
  queues: () => [...ticketKeys.all, "queue"] as const,
  queue: (filterId: number) => [...ticketKeys.queues(), filterId] as const,
  details: () => [...ticketKeys.all, "detail"] as const,
  detail: (id: number) => [...ticketKeys.details(), id] as const,
  lifetimes: () => [...ticketKeys.all, "lifetime"] as const,
  lifetime: (id: number) => [...ticketKeys.lifetimes(), id] as const,
};

// -------------------------------------------------------------
// 1. Queue Query Hook
// -------------------------------------------------------------
export function useTicketQueue(filterId: number = 984) {
  return useQuery({
    queryKey: ticketKeys.queue(filterId),
    queryFn: ({ signal }) =>
      ticketsApi.list({ filter_id: filterId, limit: 50 }, signal),
    refetchInterval: 30_000, // Silent background poll every 30s for queue freshness
  });
}

// -------------------------------------------------------------
// 1.1 Services Catalog Query Hook (Cached statically)
// -------------------------------------------------------------
export function useServicesCatalog() {
  return useQuery({
    queryKey: ["services", "catalog"] as const,
    queryFn: ({ signal }) => ticketsApi.getServices(signal),
    staleTime: Infinity,
    gcTime: 24 * 60 * 60 * 1000,
  });
}

// -------------------------------------------------------------
// 2. Ticket Detail Query Hook (with 0ms Placeholder Data)
// -------------------------------------------------------------
export function useTicketDetail(ticketId: number | null) {
  const queryClient = useQueryClient();

  return useQuery({
    queryKey: ticketKeys.detail(ticketId || 0),
    queryFn: ({ signal }) => {
      if (!ticketId) throw new Error("Ticket ID is required");
      return ticketsApi.get(ticketId, signal);
    },
    enabled: Boolean(ticketId && ticketId > 0),
    // Linear-style instant render: seed initial fields from any active queue cache
    placeholderData: (previousData) => {
      if (previousData && previousData.id === ticketId) {
        return previousData;
      }
      if (!ticketId) return undefined;

      let item: TicketListItem | undefined;
      const allQueues = queryClient.getQueriesData<TicketListItem[]>({
        queryKey: ticketKeys.queues(),
      });
      for (const [, queue] of allQueues) {
        if (Array.isArray(queue)) {
          item = queue.find((t) => t.id === ticketId);
          if (item) break;
        }
      }

      if (!item) return undefined;

      const meta = getStatusMeta(item.status_id, item.status_name);

      return {
        id: item.id,
        name: item.name,
        description: item.description || "",
        created: item.created,
        status_id: item.status_id,
        status_name: meta.name,
        priority_name: item.priority_name,
        applicant_name: item.applicant_name,
        applicant_phone: item.applicant_phone,
        applicant_email: item.applicant_email,
        service_id: item.service_id,
        service_name: item.service_name,
        pc_name: item.pc_name,
        creator_name: item.applicant_name,
        attachments: [],
        comments: [],
      } as TicketDetail;
    },
  });
}

// -------------------------------------------------------------
// 3. Ticket Lifetime Query Hook
// -------------------------------------------------------------
export function useTicketLifetime(ticketId: number | null) {
  return useQuery({
    queryKey: ticketKeys.lifetime(ticketId || 0),
    queryFn: ({ signal }) => {
      if (!ticketId) return [] as TicketLifetimeEvent[];
      return ticketsApi.getLifetime(ticketId, signal).catch((err) => {
        if (err.name === "AbortError") throw err;
        return [] as TicketLifetimeEvent[];
      });
    },
    enabled: Boolean(ticketId && ticketId > 0),
  });
}

// -------------------------------------------------------------
// Helper: Optimistic Ticket Status & Queue Updater
// -------------------------------------------------------------
export interface TicketCacheUpdate {
  status_id?: number;
  status_name?: string;
  service_id?: number;
  service_name?: string;
}

function updateTicketInCache(
  queryClient: QueryClient,
  ticketId: number,
  updates: TicketCacheUpdate
) {
  // 1. Update Detail Cache
  queryClient.setQueryData<TicketDetail>(ticketKeys.detail(ticketId), (old) => {
    if (!old) return old;
    return { ...old, ...updates };
  });

  // 2. Update all active queues in cache
  const queueQueries = queryClient.getQueriesData<TicketListItem[]>({
    queryKey: ticketKeys.queues(),
  });
  for (const [key, list] of queueQueries) {
    if (Array.isArray(list)) {
      queryClient.setQueryData<TicketListItem[]>(key, (oldList) => {
        if (!oldList) return oldList;
        return oldList.map((t) => (t.id === ticketId ? { ...t, ...updates } : t));
      });
    }
  }
}

// -------------------------------------------------------------
// 4. Ticket Mutation Hooks (with Instant Optimistic UI)
// -------------------------------------------------------------
export function useTicketActions() {
  const queryClient = useQueryClient();

  const takeMutation = useMutation({
    mutationFn: (id: number) => ticketsApi.take(id),
    onSuccess: (_, id) => {
      updateTicketInCache(queryClient, id, { status_id: 2, status_name: "В работе" });
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({ id, comment }: { id: number; comment: string }) =>
      ticketsApi.resolve(id, comment),
    onSuccess: (_, { id }) => {
      updateTicketInCache(queryClient, id, { status_id: 3, status_name: "Выполнена" });
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: ({
      id,
      masterId,
      comment,
    }: {
      id: number;
      masterId: number;
      comment: string;
    }) => ticketsApi.cancel(id, comment, `Дубликат заявки #${masterId}`),
    onSuccess: (_, { id }) => {
      updateTicketInCache(queryClient, id, { status_id: 30, status_name: "Отменена" });
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });

  const redirectMutation = useMutation({
    mutationFn: ({
      id,
      serviceId,
      comment,
    }: {
      id: number;
      serviceId: number;
      comment: string;
    }) => ticketsApi.redirect(id, serviceId, comment),
    onSuccess: (_, { id }) => {
      updateTicketInCache(queryClient, id, { status_id: 30, status_name: "Отменена" });
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });

  const addCommentMutation = useMutation({
    mutationFn: ({
      id,
      comment,
      isPrivate,
    }: {
      id: number;
      comment: string;
      isPrivate: boolean;
    }) => ticketsApi.addComment(id, comment, isPrivate),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ticketKeys.detail(id) });
      queryClient.invalidateQueries({ queryKey: ticketKeys.lifetime(id) });
    },
  });

  return {
    takeMutation,
    resolveMutation,
    duplicateMutation,
    redirectMutation,
    addCommentMutation,
  };
}
