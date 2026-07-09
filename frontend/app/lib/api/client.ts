import createFetchClient, { type Middleware } from "openapi-fetch";

import { clearToken, getToken } from "~/lib/auth/token";
import { API_BASE_URL } from "~/lib/config";
import type { paths } from "./schema";

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken();
    if (token) {
      request.headers.set("Authorization", `Bearer ${token}`);
    }
    return request;
  },
  async onResponse({ response }) {
    // Token expired/tampered → drop it so the guard bounces to /login.
    if (response.status === 401) {
      clearToken();
    }
    return response;
  },
};

export const fetchClient = createFetchClient<paths>({ baseUrl: API_BASE_URL });
fetchClient.use(authMiddleware);
