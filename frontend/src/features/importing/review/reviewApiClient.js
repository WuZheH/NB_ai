import { getJson, postJson } from "../../../shared/api/client.js";
import { createReviewApi } from "./reviewApi.js";

export const reviewApi = createReviewApi({ get: getJson, post: postJson });
