import { Navigate } from "react-router";

import { Button } from "~/components/ui/button";
import { getToken } from "~/lib/auth/token";
import { API_BASE_URL } from "~/lib/config";

export default function Login() {
  if (getToken() !== null) {
    return <Navigate to="/app" replace />;
  }

  return (
    <main className="flex min-h-screen bg-background">
      {/* Left Pane - Hero Image */}
      <div className="relative hidden w-1/2 lg:block">
        <img
          src="/images/orca-hero.jpg"
          alt="Orca surfacing in the ocean"
          className="absolute inset-0 h-full w-full object-cover"
        />
        <div className="absolute inset-0 bg-black/20" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/40 to-transparent" />
        
        <div className="absolute bottom-16 left-16 right-16">
          <h2 className="font-serif text-4xl font-bold tracking-tight text-white drop-shadow-sm">
            Orchestrate your workspace.
          </h2>
          <p className="mt-4 max-w-lg text-lg text-zinc-300 drop-shadow">
            Connect Gmail, Calendar, and Drive with intelligent, agentic automation. Experience the next generation of enterprise productivity.
          </p>
        </div>
      </div>

      {/* Right Pane - Login Form */}
      <div className="relative flex w-full flex-col justify-center px-8 lg:w-1/2 lg:px-24 xl:px-32">
        {/* Mobile background treatment */}
        <div className="absolute inset-0 lg:hidden">
          <img
            src="/images/orca-hero.jpg"
            alt="Orca"
            className="h-full w-full object-cover opacity-10 dark:opacity-5"
          />
          <div className="absolute inset-0 bg-gradient-to-b from-background/80 via-background to-background" />
        </div>

        <div className="relative z-10 mx-auto w-full max-w-sm">
          <div className="mb-10">
            <h1 className="font-serif text-5xl font-bold tracking-tight text-foreground">Orca</h1>
            <p className="mt-3 text-sm font-medium uppercase tracking-wider text-muted-foreground">
              Enterprise Intelligence
            </p>
          </div>

          <div className="space-y-6">
            <Button
              size="lg"
              className="w-full font-semibold transition-transform active:scale-[0.98]"
              type="button"
              onClick={() => {
                window.location.href = `${API_BASE_URL}/api/v1/auth/google`;
              }}
            >
              <svg
                className="mr-2 h-5 w-5"
                aria-hidden="true"
                focusable="false"
                data-prefix="fab"
                data-icon="google"
                role="img"
                xmlns="http://www.w3.org/2000/svg"
                viewBox="0 0 488 512"
              >
                <path
                  fill="currentColor"
                  d="M488 261.8C488 403.3 391.1 504 248 504 110.8 504 0 393.2 0 256S110.8 8 248 8c66.8 0 123 24.5 166.3 64.9l-67.5 64.9C258.5 52.6 94.3 116.6 94.3 256c0 86.5 69.1 156.6 153.7 156.6 98.2 0 135-70.4 140.8-106.9H248v-85.3h236.1c2.3 12.7 3.9 24.9 3.9 41.4z"
                />
              </svg>
              Sign in with Google
            </Button>

            <p className="text-center text-xs text-muted-foreground">
              By signing in, you agree to our{" "}
              <a href="#" className="underline underline-offset-4 hover:text-foreground">Terms of Service</a>
              {" "}and{" "}
              <a href="#" className="underline underline-offset-4 hover:text-foreground">Privacy Policy</a>.
            </p>
          </div>
        </div>
      </div>
    </main>
  );
}
