import type { Hit } from "../types";

/** chunk_id → rank within one result list. */
export const rankMap = (hits: Hit[]): Map<number, number> => new Map(hits.map((h) => [h.chunk_id, h.rank]));
