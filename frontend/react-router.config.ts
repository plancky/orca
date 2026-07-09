import type { Config } from "@react-router/dev/config";

// SPA mode: no runtime server. React Router still pre-renders the root route at
// BUILD time to emit build/client/index.html; everything else is client-routed.
export default {
  ssr: false,
} satisfies Config;
