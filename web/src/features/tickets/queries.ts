import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ticketsApi,
  TicketListItem,
  TicketDetail,
  TicketLifetimeEvent,
} from "@/shared/api";

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
    // Linear-style instant render: seed initial fields from the queue cache
    placeholderData: (previousData) => {
      if (previousData && previousData.id === ticketId) {
        return previousData;
      }
      if (!ticketId) return undefined;

      const queueItems = queryClient.getQueryData<TicketListItem[]>(
        ticketKeys.queue(984)
      );
      const item = queueItems?.find((t) => t.id === ticketId);
      if (!item) return undefined;

      return {
        id: item.id,
        name: item.name,
        description: "",
        created: item.created,
        status_id: item.status_id,
        status_name: item.status_name,
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
// 4. Ticket Mutation Hooks
// -------------------------------------------------------------
export function useTicketActions() {
  const queryClient = useQueryClient();

  const takeMutation = useMutation({
    mutationFn: (id: number) => ticketsApi.take(id),
    onSuccess: (_, id) => {
      // Optimistically update status in queue & detail
      queryClient.setQueryData<TicketDetail>(ticketKeys.detail(id), (old) => {
        if (!old) return old;
        return { ...old, status_id: 2, status_name: "В работе" };
      });
      queryClient.setQueryData<TicketListItem[]>(ticketKeys.queue(984), (old) => {
        if (!old) return old;
        return old.map((t) =>
          t.id === id ? { ...t, status_id: 2, status_name: "В работе" } : t
        );
      });
      // Invalidate to guarantee full backend sync
      queryClient.invalidateQueries({ queryKey: ticketKeys.all });
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({ id, comment }: { id: number; comment: string }) =>
      ticketsApi.resolve(id, comment),
    onSuccess: () => {
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
    onSuccess: () => {
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
    onSuccess: () => {
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
