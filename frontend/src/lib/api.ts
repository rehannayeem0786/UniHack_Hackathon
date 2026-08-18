/**
 * Typed client for the enrichment service.
 *
 * Requests go to the same origin: in production FastAPI serves the built
 * bundle, and in development Vite proxies /api. So there is no base URL to
 * configure and no CORS to reason about.
 */

export interface HealthPayload {
  status: string;
  llm: { providers: string[]; configured: boolean };
  dataset: {
    labelled_rows: number;
    training_rows: number;
    holdout_rows: number;
    holdout_ratio: number;
  };
  knowledge_base: Record<string, number>;
  delivery_columns: number;
  stages: string[];
}

export interface AttributePayload {
  label: string;
  value: string | null;
  uom: string | null;
  confidence: number;
  source: string;
}

/** One first-party document the research stage actually read. */
export interface CitationPayload {
  url: string;
  kind: string;
  title: string;
  retrieved_at: string;
  from_cache: boolean;
  characters: number;
  table_rows: number;
  /** Trust tier: `first-party` (manufacturer) or `third-party` (fallback). */
  source?: string;
}

export interface RecordPayload {
  part_number: string;
  sku: string;
  input: {
    description: string;
    mpn: string;
    supplier: string;
    dept: string;
    class: string;
    fine: string;
    brand_hints: string[];
  };
  output: {
    manufacturer_name: string | null;
    brand_name: string | null;
    mpn: string | null;
    classpath: string | null;
    product_name: string | null;
    series: string | null;
    with_clause: string | null;
    invoice_desc: string | null;
    mobile_desc: string | null;
    short_desc: string | null;
    long_desc: string | null;
    retail_desc: string | null;
    marketing_desc: string | null;
    mfr_url: string | null;
  };
  attributes: AttributePayload[];
  features: string[];
  approvals: string[];
  extras: Record<string, string>;
  confidence: Record<string, number>;
  provenance: Record<string, string>;
  issues: string[];
  needs_review: boolean;
  truth?: Record<string, string>;
  /** Documents retrieved from the manufacturer's own site for this record. */
  citations: CitationPayload[];
  /** Attribute label (or delivery column) -> the source URL it was verified against. */
  grounded: Record<string, string>;
  /** Why retrieval found nothing, when it found nothing. */
  research_note?: string;
}

export interface CostPayload {
  estimated_usd: number;
  usd_per_record: number;
  live_tokens: number;
  live_calls: number;
  cache_hits: number;
  estimated_cache_savings_usd: number;
  usd_per_record_without_cache: number;
  basis: string;
}

export interface PipelineSummary {
  records: number;
  elapsed_seconds: number;
  seconds_per_record: number;
  needs_review: number;
  review_rate: number;
  mean_confidence: number;
  total_issues: number;
  stage_failures: Record<string, number>;
  llm: {
    calls: number;
    cache_hits: number;
    live_calls: number;
    failures: number;
    total_tokens: number;
    cache_hit_rate: number;
    by_model: Record<string, number>;
  };
  /** Estimated USD for this run, and what the cache saved. */
  cost?: CostPayload;
  sourced_records: number;
  sourced_rate: number;
  documents_read: number;
  grounded_attributes: number;
  grounded_rate: number;
  retrieval: {
    requests: number;
    cache_hits: number;
    live_requests: number;
    failures: number;
    blocked_by_policy: number;
    robots_denied: number;
    megabytes: number;
    avg_latency_s: number;
    cache_hit_rate: number;
  };
}

export interface FieldScore {
  field: string;
  compared: number;
  exact_match: number;
  fuzzy_match: number;
  mean_similarity: number;
  fill_rate: number;
  truth_fill_rate: number;
}

export interface Metrics {
  rows: number;
  fields: FieldScore[];
  headline: { mean_exact_match: number; mean_fuzzy_match: number };
  attributes: {
    labels_emitted: number;
    label_precision: number;
    values_compared: number;
    value_accuracy: number;
  };
  compliance: Record<string, number>;
  coverage: {
    cells_filled: number;
    truth_cells_filled: number;
    fill_ratio_vs_truth: number;
  };
  /** Present when the run carried records, i.e. everywhere but a reloaded metrics file. */
  sourcing?: {
    records: number;
    records_with_a_source: number;
    sourced_rate: number;
    records_with_verified_source: number;
    documents_read: number;
    documents_by_kind: Record<string, number>;
    third_party_documents: number;
    records_supplemented_third_party: number;
    deep_product_links: number;
    deep_link_rate: number;
    filled_attribute_values: number;
    grounded_values: number;
    grounded_rate: number;
  };
}

export interface EvaluationPayload {
  fold: string;
  rows: number;
  pipeline: PipelineSummary;
  metrics: Metrics;
}

export interface JobState {
  id: string;
  label: string;
  status: "queued" | "running" | "done" | "failed";
  done: number;
  total: number;
  progress: number;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  summary: PipelineSummary | Record<string, never>;
  has_metrics: boolean;
}

export interface SamplePayload {
  fold: string;
  count: number;
  rows: Record<string, string>[];
}

/** One event from `/api/enrich/stream`, in arrival order. */
export type EnrichStreamEvent =
  | {
      type: "stage";
      stage: string;
      event: "start" | "end";
      elapsed_ms: number;
      part_number: string;
    }
  | { type: "record"; record: RecordPayload }
  | { type: "summary"; summary: PipelineSummary }
  | { type: "error"; message: string };

export interface KnowledgePayload {
  summary: Record<string, number>;
  classpaths: string[];
  templates: Record<string, string[]>;
  brands: Record<string, string>;
  mpn_prefixes: Record<string, string>;
  description_tokens: Record<string, string>;
  sourcing_domains: Record<string, string>;
  invoice_abbreviations: Record<string, string>;
  approvals: string[];
}

export interface StylePayload {
  classpath: string;
  template: string[];
  surfaces: Record<
    string,
    {
      rows_learned_from: number;
      order: string[];
      suffixes: Record<string, [string, string]>;
    }
  >;
}

/** One line in the review queue. */
export interface ReviewQueueRow {
  part_number: string;
  brand: string;
  product_name: string;
  description: string;
  needs_review: boolean;
  confidence: number;
  issues: string[];
  status: "pending" | "approved" | "corrected";
  sourced: boolean;
}

export interface ReviewQueuePayload {
  fold: string;
  status: string;
  total: number;
  rows: ReviewQueueRow[];
}

export interface ReviewSummaryPayload {
  corrections: {
    decisions: number;
    approved: number;
    corrected: number;
    field_overrides: number;
    attribute_overrides: number;
  };
  reviewable_fields: string[];
}

/** A stored reviewer decision, as returned by the API. */
export interface ReviewDecisionPayload {
  part_number: string;
  decided_at: string;
  status: "approved" | "corrected";
  fields: Record<string, string | null>;
  attributes: { label: string; value: string | null; uom: string | null }[];
  extras: Record<string, string>;
  notes: string;
}

export interface ReviewRecordPayload extends RecordPayload {
  review_status: "pending" | "approved" | "corrected";
  decision: ReviewDecisionPayload | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: init?.body instanceof FormData ? {} : { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      "Cannot reach the enrichment service. Is it running on port 8000?",
      0,
    );
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* the body was not JSON; the status line is the best we have */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthPayload>("/api/health"),
  knowledge: () => request<KnowledgePayload>("/api/knowledge"),
  style: (classpath: string) =>
    request<StylePayload>(`/api/style/${encodeURI(classpath)}`),
  sample: (limit = 40, fold = "holdout") =>
    request<SamplePayload>(`/api/sample?limit=${limit}&fold=${fold}`),

  enrich: (partNumbers: string[]) =>
    request<{ summary: PipelineSummary; records: RecordPayload[] }>("/api/enrich", {
      method: "POST",
      body: JSON.stringify({ part_numbers: partNumbers }),
    }),

  /**
   * Enrich one row while streaming stage transitions as Server-Sent Events.
   *
   * `EventSource` only does GET, and this needs a POST body, so the stream is
   * read with fetch and parsed by hand. The callback fires once per event:
   * `stage` (start/end with real elapsed ms), then `record`, then `summary`.
   * Returns a promise that resolves when the stream closes, or rejects on a
   * network failure or a server-side `error` event.
   */
  enrichStream: async (
    partNumber: string,
    onEvent: (event: EnrichStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch("/api/enrich/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ part_numbers: [partNumber] }),
      signal,
    });
    if (!response.ok || !response.body) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const body = (await response.json()) as { detail?: string };
        if (body?.detail) detail = body.detail;
      } catch {
        /* not JSON */
      }
      throw new ApiError(detail, response.status);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line; each is `event:` + `data:` lines.
      // The event type also travels inside the JSON payload, so only `data:`
      // needs parsing here.
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        let data = "";
        for (const line of raw.split("\n")) {
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;

        const parsed = JSON.parse(data) as EnrichStreamEvent;
        if (parsed.type === "error") throw new ApiError(parsed.message, 500);
        onEvent(parsed);
      }
    }
  },

  evaluation: (refresh = false) =>
    request<EvaluationPayload>(`/api/evaluation?refresh=${refresh}`),

  startDatasetJob: (fold: string, limit?: number) =>
    request<JobState>("/api/jobs/dataset", {
      method: "POST",
      body: JSON.stringify({ fold, limit: limit ?? null }),
    }),

  uploadJob: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<JobState>("/api/jobs/upload", { method: "POST", body: form });
  },

  job: (id: string) => request<JobState>(`/api/jobs/${id}`),

  jobResults: (id: string, offset = 0, limit = 50) =>
    request<{
      id: string;
      total: number;
      offset: number;
      records: RecordPayload[];
      summary: PipelineSummary;
    }>(`/api/jobs/${id}/results?offset=${offset}&limit=${limit}`),

  jobMetrics: (id: string) => request<Metrics>(`/api/jobs/${id}/metrics`),

  exportUrl: (id: string) => `/api/jobs/${id}/export`,

  // --- review queue ---
  reviewSummary: () => request<ReviewSummaryPayload>("/api/review/summary"),

  reviewQueue: (fold = "holdout", status = "pending", limit = 100) =>
    request<ReviewQueuePayload>(
      `/api/review/queue?fold=${fold}&status=${status}&limit=${limit}`,
    ),

  reviewRecord: (partNumber: string, fold = "holdout") =>
    request<ReviewRecordPayload>(
      `/api/review/${encodeURIComponent(partNumber)}?fold=${fold}`,
    ),

  reviewDecision: (
    partNumber: string,
    decision: {
      status: "approved" | "corrected";
      fields?: Record<string, string | null>;
      attributes?: { label: string; value: string | null; uom: string | null }[];
      extras?: Record<string, string>;
      notes?: string;
    },
  ) =>
    request<{ part_number: string; decision: ReviewDecisionPayload }>(
      `/api/review/${encodeURIComponent(partNumber)}/decision`,
      { method: "POST", body: JSON.stringify(decision) },
    ),
};
