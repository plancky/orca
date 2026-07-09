import { useEffect } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Skeleton } from "~/components/ui/skeleton";
import { setToken } from "~/lib/auth/token";

export default function AuthCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    // Run exactly once on mount. The hash includes the leading "#".
    const hash = window.location.hash;
    const params = new URLSearchParams(hash.slice(1));
    const token = params.get("token");
    const error = params.get("error");

    if (error) {
      toast.error(`Authentication failed: ${error}`);
      navigate("/login", { replace: true });
      return;
    }

    if (token) {
      setToken(token);
      navigate("/app", { replace: true });
      return;
    }

    // No token and no error, unexpected state.
    navigate("/login", { replace: true });
  }, [navigate]);

  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <Skeleton className="h-8 w-48" />
    </div>
  );
}