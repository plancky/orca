import type { ReactNode } from "react";
import { Navigate } from "react-router";

import { Skeleton } from "~/components/ui/skeleton";
import { $api } from "~/lib/api/query";
import { getToken } from "~/lib/auth/token";

/**
 * Gates the authed layout. On mount, if a token exists it is validated against
 * `POST /login/test-token`; a 401 clears the token (middleware) and the query
 * errors → bounce to /login. No token at all → straight to /login.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const token = getToken();
  const { data, isLoading, isError } = $api.useQuery(
    "post",
    "/api/v1/login/test-token",
    {},
    { enabled: token !== null, retry: false, staleTime: 5 * 60 * 1000 },
  );

  if (token === null) {
    return <Navigate to="/login" replace />;
  }
  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Skeleton className="h-8 w-48" />
      </div>
    );
  }
  if (isError || !data) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
