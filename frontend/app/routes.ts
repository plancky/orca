import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/_index.tsx"),
  route("login", "routes/login.tsx"),
  route("auth/callback", "routes/auth.callback.tsx"),
  route("app", "routes/app.tsx", [
    index("routes/app._index.tsx"),
    route("c/:conversationId", "routes/app.c.$conversationId.tsx"),
  ]),
] satisfies RouteConfig;
