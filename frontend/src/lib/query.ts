import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Never retry auth/permission/validation failures.
        if (error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) return false;
        return failureCount < 2;
      },
    },
    mutations: { retry: 0 },
  },
});

export function invalidate(prefix: string[]) {
  return queryClient.invalidateQueries({ queryKey: prefix });
}
