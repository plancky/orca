import { Outlet } from "react-router";

import { ConversationList } from "~/components/history/ConversationList";
import { StatusBar } from "~/components/status/StatusBar";
import { RequireAuth } from "~/lib/auth/guard";

export default function AppLayout() {
  return (
    <RequireAuth>
      <div className="flex h-screen flex-col">
        <header className="flex items-center justify-between gap-4 border-b px-4 py-2">
          <span className="font-serif text-lg font-semibold tracking-tight">
            Orca
          </span>
          <StatusBar />
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
