import createClient from "openapi-react-query";

import { fetchClient } from "./client";

// Every server-state hook derives from this: $api.useQuery / $api.useMutation
// are compile-time checked against the generated OpenAPI `paths`.
export const $api = createClient(fetchClient);
