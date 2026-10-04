import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type User } from "./api";

export function useCurrentUser() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<User>("/api/auth/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return async () => {
    await api("/api/auth/logout", { method: "POST" });
    queryClient.clear();
    queryClient.setQueryData(["me"], null);
  };
}
