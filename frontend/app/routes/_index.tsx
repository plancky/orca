import { Navigate } from "react-router";

import { getToken } from "~/lib/auth/token";

export default function Index() {
  return <Navigate to={getToken() !== null ? "/app" : "/login"} replace />;
}
