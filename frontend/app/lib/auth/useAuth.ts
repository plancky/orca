import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

import { $api } from "~/lib/api/query";
import { clearToken, getToken, setToken } from "~/lib/auth/token";

/**
 * Auth surface for the SPA.
 *
 * `login` is the one call that opts out of JSON: `POST /login/access-token` is
 * `application/x-www-form-urlencoded` (OAuth2 password flow), so it uses a
 * URLSearchParams bodySerializer. On success the bearer token is stored and all
 * queries are invalidated. No refresh flow exists backend-side (8-day expiry →
 * re-login), so bootstrap validation lives in the route guard via `test-token`.
 */
export function useAuth() {
  const queryClient = useQueryClient();
  const loginMutation = $api.useMutation("post", "/api/v1/login/access-token");

  const login = useCallback(
    async (username: string, password: string): Promise<void> => {
      const data = await loginMutation.mutateAsync({
        body: { username, password, scope: "" },
        bodySerializer: (body: Record<string, unknown>) =>
          new URLSearchParams(body as Record<string, string>),
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      setToken(data.access_token);
      await queryClient.invalidateQueries();
    },
    [loginMutation, queryClient],
  );

  const logout = useCallback(() => {
    clearToken();
    queryClient.clear();
  }, [queryClient]);

  return {
    login,
    logout,
    isLoggingIn: loginMutation.isPending,
    loginError: loginMutation.error,
    hasToken: getToken() !== null,
  };
}
