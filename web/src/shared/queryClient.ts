import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60 * 1000, // 1 minute fresh window (avoids excessive IntraService API calls)
      gcTime: 10 * 60 * 1000, // 10 minutes in-memory cache
      refetchOnWindowFocus: false, // Do not refetch on alt-tab
      retry: 1,
    },
  },
});
