import { LogOut } from "lucide-react";
import { Outlet, useNavigate } from "react-router";

import { ConversationList } from "~/components/history/ConversationList";
import { StatusBar } from "~/components/status/StatusBar";
import { Button } from "~/components/ui/button";
import { RequireAuth } from "~/lib/auth/guard";
import { useAuth } from "~/lib/auth/useAuth";

export default function AppLayout() {
  const { logout } = useAuth();
  const navigate = useNavigate();

  function onLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <RequireAuth>
      <div className="flex h-screen flex-col">
        <header className="flex items-center justify-between gap-4 border-b px-4 py-2">
          <div className="flex items-center gap-4">
            <span className="font-semibold">Orca</span>
            <StatusBar />
          </div>
          <Button variant="ghost" size="sm" onClick={onLogout}>
            <LogOut className="size-4" />
            <span className="ml-1">Logout</span>
          </Button>
        </header>
        <div className="flex min-h-0 flex-1">
          <ConversationList />
          <main className="min-w-0 flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </RequireAuth>
  );
}
