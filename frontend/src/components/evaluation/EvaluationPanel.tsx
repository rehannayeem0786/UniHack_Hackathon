import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  BarChart3,
  BookOpenCheck,
  CircleCheck,
  Database,
  Gauge,
  RefreshCw,
  Target,
} from "lucide-react";

import { MetricCard } from "@/components/shared/MetricCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/misc";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api, type FieldScore, type Metrics, type PipelineSummary } from "@/lib/api";
import { fieldLabel, humanKey, integer, percent } from "@/lib/format";

const IDENTITY_FIELDS = new Set([
  "MANUFACTURER_NAME",
  "BRAND_NAME",
  "MANUFACTURER_PART_NUMBER",
  "Classpath",
]);

/*
 * Series colours are fixed and fully opaque. Recharts colours its default
 * tooltip text from each bar's fill, so a translucent or dark fill produces a
 * legible bar and an illegible tooltip. Three solid, distinguishable colours
 * fix both at once, and the tooltip below sets its own text colour regardless.
 */
const SERIES = [
  {
    key: "similarity" as const,
    label: "Mean similarity",
    color: "hsl(215 22% 58%)",
    hint: "Average token overlap with the reference",
  },
  {
    key: "fuzzy" as const,
    label: "Fuzzy match",
    color: "hsl(217 91% 62%)",
    hint: "Passes the similarity threshold",
  },
  {
    key: "exact" as const,
    label: "Exact match",
    color: "hsl(158 64% 45%)",
    hint: "Character-for-character equality",
  },
];

interface ChartRow {
  name: string;
  exact: number;
  fuzzy: number;
  similarity: number;
  identity: boolean;
}

function ChartLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {SERIES.map((series) => (
        <li key={series.key} className="flex items-center gap-2 text-xs">
          <span
            className="size-2.5 shrink-0 rounded-sm"
            style={{ backgroundColor: series.color }}
            aria-hidden
          />
          <span className="font-medium text-foreground/90">{series.label}</span>
          <span className="text-muted-foreground">— {series.hint}</span>
        </li>
      ))}
    </ul>
  );
}

function AccuracyTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { payload: ChartRow }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;

  return (
    <div className="min-w-48 rounded-md border border-border bg-popover/95 p-3 shadow-lift backdrop-blur">
      <p className="mb-2 flex items-center gap-2 text-sm font-semibold text-popover-foreground">
        {label}
        {row.identity ? (
          <span className="rounded-full border border-border px-1.5 py-px text-[0.5625rem] font-medium uppercase tracking-wide text-muted-foreground">
            identity
          </span>
        ) : null}
      </p>
      <dl className="space-y-1">
        {SERIES.map((series) => (
          <div key={series.key} className="flex items-center justify-between gap-4 text-xs">
            <dt className="flex items-center gap-2 text-muted-foreground">
              <span
                className="size-2 shrink-0 rounded-sm"
                style={{ backgroundColor: series.color }}
                aria-hidden
              />
              {series.label}
            </dt>
            <dd className="tabular font-semibold text-popover-foreground">
              {row[series.key]}%
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** Axis label, tinted emerald for the four identity fields. */
function FieldTick({
  x,
  y,
  payload,
  identityNames,
}: {
  x?: number;
  y?: number;
  payload?: { value: string };
  identityNames: Set<string>;
}) {
  const value = payload?.value ?? "";
  const isIdentity = identityNames.has(value);
  return (
    <text
      x={x}
      y={y}
      dy={10}
      textAnchor="end"
      transform={`rotate(-32, ${x}, ${y})`}
      fill={isIdentity ? "hsl(158 55% 62%)" : "hsl(215 18% 66%)"}
      fontSize={11}
      fontWeight={isIdentity ? 600 : 400}
    >
      {value}
    </text>
  );
}

function AccuracyChart({ fields }: { fields: FieldScore[] }) {
  const data: ChartRow[] = fields.map((field) => ({
    name: fieldLabel(field.field).replace(" description", ""),
    exact: Math.round(field.exact_match * 100),
    fuzzy: Math.round(field.fuzzy_match * 100),
    similarity: Math.round(field.mean_similarity * 100),
    identity: IDENTITY_FIELDS.has(field.field),
  }));

  const identityNames = new Set(data.filter((d) => d.identity).map((d) => d.name));

  return (
    <div className="space-y-3">
      <ChartLegend />
      <div className="h-80 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 56, left: -18 }}>
            <defs>
              {SERIES.map((series) => (
                <linearGradient id={`grad-${series.key}`} key={series.key} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={series.color} stopOpacity={1} />
                  <stop offset="100%" stopColor={series.color} stopOpacity={0.55} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="hsl(220 28% 20%)"
              vertical={false}
            />
            <XAxis
              dataKey="name"
              interval={0}
              height={72}
              stroke="hsl(220 28% 20%)"
              tick={<FieldTick identityNames={identityNames} />}
            />
            <YAxis
              domain={[0, 100]}
              unit="%"
              tick={{ fill: "hsl(215 18% 66%)", fontSize: 11 }}
              stroke="hsl(220 28% 20%)"
            />
            <ChartTooltip
              cursor={{ fill: "hsl(217 91% 60% / 0.07)" }}
              content={<AccuracyTooltip />}
            />
            {SERIES.map((series) => (
              <Bar
                key={series.key}
                dataKey={series.key}
                name={series.label}
                radius={[4, 4, 0, 0]}
                fill={`url(#grad-${series.key})`}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-foreground">
        Emerald axis labels are the four identity fields, held to a stricter
        standard than the descriptions.
      </p>
    </div>
  );
}

function ComplianceList({ compliance }: { compliance: Record<string, number> }) {
  return (
    <ul className="space-y-2.5">
      {Object.entries(compliance).map(([key, value]) => {
        const pass = value >= 0.995;
        return (
          <li key={key} className="flex items-center justify-between gap-3">
            <span className="text-sm text-foreground/85">{humanKey(key)}</span>
            <div className="flex shrink-0 items-center gap-2">
              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-secondary">
                <div
                  className={pass ? "h-full rounded-full bg-success" : "h-full rounded-full bg-warning"}
                  style={{ width: `${Math.round(value * 100)}%` }}
                />
              </div>
              <Badge variant={pass ? "success" : "warning"} className="tabular w-14 justify-center">
                {percent(value, 0)}
              </Badge>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function EvaluationPanel() {
  const evaluation = useAsync(() => api.evaluation(false), { immediate: false });
  const data = evaluation.data;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <CardTitle as="h2">Holdout evaluation</CardTitle>
              <CardDescription className="max-w-3xl">
                Every figure here is measured on rows excluded from the knowledge base.
                The split is a hash of the part number, so it is identical on every
                machine and cannot be tuned. Fitting the registries on all 200 labelled
                rows and then scoring on those same rows would inflate these numbers,
                which is why the pipeline only ever learns from the training fold.
              </CardDescription>
            </div>
            <div className="flex gap-2">
              <Button onClick={() => void evaluation.run()} loading={evaluation.loading}>
                {!evaluation.loading ? <Target className="size-4" aria-hidden /> : null}
                {data ? "Reload" : "Run evaluation"}
              </Button>
              {data ? (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Recompute from scratch"
                  onClick={() => void api.evaluation(true).then(() => evaluation.run())}
                >
                  <RefreshCw className="size-4" aria-hidden />
                </Button>
              ) : null}
            </div>
          </div>
        </CardHeader>

        {evaluation.loading ? (
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-24" />
              ))}
            </div>
            <Skeleton className="h-72" />
            <p className="text-sm text-muted-foreground">
              Enriching the holdout fold. Cached model responses make this fast on a
              second run; the first run makes live calls.
            </p>
          </CardContent>
        ) : evaluation.error ? (
          <CardContent>
            <ErrorState
              message={evaluation.error}
              action={
                <Button size="sm" variant="secondary" onClick={() => void evaluation.run()}>
                  Retry
                </Button>
              }
            />
          </CardContent>
        ) : !data ? (
          <CardContent>
            <EmptyState
              icon={BarChart3}
              title="Not evaluated yet"
              description="Run the evaluation to score the held-out fold on field accuracy, attribute agreement, house-rule compliance and schema coverage."
            />
          </CardContent>
        ) : null}
      </Card>

      {data ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Mean exact match"
              value={percent(data.metrics.headline.mean_exact_match, 1)}
              detail="Character-for-character agreement across ten scored fields"
              hint="Exact string equality after whitespace normalisation. A 400-character product-page description must match every word to count here."
              icon={Target}
              tone="primary"
              index={0}
            />
            <MetricCard
              label="Mean fuzzy match"
              value={percent(data.metrics.headline.mean_fuzzy_match, 1)}
              detail="Allows different word order in descriptions"
              hint="Token-set similarity at or above 85. The four identity fields still need 97, because a near-miss on a brand name is simply wrong."
              icon={CircleCheck}
              tone="success"
              index={1}
            />
            <MetricCard
              label="Schema coverage"
              value={percent(data.metrics.coverage.fill_ratio_vs_truth, 1)}
              detail={`${integer(data.metrics.coverage.cells_filled)} of ${integer(data.metrics.coverage.truth_cells_filled)} cells a human filled`}
              hint="Completeness without accuracy is worthless and accuracy without completeness is a cherry-pick, so both are reported."
              icon={Database}
              index={2}
            />
            <MetricCard
              label="Rows flagged"
              value={percent(data.pipeline.review_rate, 0)}
              detail={`${data.pipeline.needs_review} of ${data.rows} rows sent to human review`}
              hint="Rows whose weighted confidence fell below the review threshold. Reporting a gap beats filling it with a guess."
              icon={Gauge}
              tone="warning"
              index={3}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle as="h3">Field accuracy</CardTitle>
              <CardDescription>
                Three measures per field, from the most forgiving to the strictest. Hover a
                group for the exact figures.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <AccuracyChart fields={data.metrics.fields} />

              <Table caption="Per-field accuracy against ground truth">
                <TableHeader>
                  <TableRow>
                    <TableHead>Field</TableHead>
                    <TableHead className="text-right">Compared</TableHead>
                    <TableHead className="text-right">Exact</TableHead>
                    <TableHead className="text-right">Fuzzy</TableHead>
                    <TableHead className="text-right">Similarity</TableHead>
                    <TableHead className="text-right">Filled</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.metrics.fields.map((field) => (
                    <TableRow key={field.field}>
                      <TableCell className="font-medium">
                        <div className="flex items-center gap-2">
                          {fieldLabel(field.field)}
                          {IDENTITY_FIELDS.has(field.field) ? (
                            <Badge variant="outline" className="text-[0.5625rem]">
                              identity
                            </Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="tabular text-right text-muted-foreground">
                        {field.compared}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {percent(field.exact_match, 0)}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {percent(field.fuzzy_match, 0)}
                      </TableCell>
                      <TableCell className="tabular text-right text-muted-foreground">
                        {percent(field.mean_similarity, 0)}
                      </TableCell>
                      <TableCell className="tabular text-right text-muted-foreground">
                        {percent(field.fill_rate, 0)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
                Description fields score high on similarity and low on exact match, and
                that gap is the honest story of this dataset. The reference descriptions
                contain specifications — amperage, drum material, annual energy use — that
                appear nowhere in the one-line input and can only come from the
                manufacturer's own documentation. The pipeline reproduces the structure,
                ordering and house style correctly; it cannot invent facts it was never
                given, and it is designed not to try.
              </p>
            </CardContent>
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle as="h3">House-rule compliance</CardTitle>
                <CardDescription>
                  Measurable without any ground truth, so it applies to a catalogue that
                  has never been labelled.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ComplianceList compliance={data.metrics.compliance} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle as="h3">Attributes &amp; throughput</CardTitle>
                <CardDescription>
                  Label precision is how often an emitted attribute label belongs to the
                  category at all; value accuracy is how often its value is right.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <dl className="space-y-0 text-sm">
                  {(
                    [
                      ["Attribute labels emitted", integer(data.metrics.attributes.labels_emitted)],
                      ["Label precision", percent(data.metrics.attributes.label_precision, 1)],
                      ["Values compared", integer(data.metrics.attributes.values_compared)],
                      ["Value accuracy", percent(data.metrics.attributes.value_accuracy, 1)],
                      ["Seconds per row", `${data.pipeline.seconds_per_record}`],
                      ["Live model calls", integer(data.pipeline.llm.live_calls)],
                      ["Cache hit rate", percent(data.pipeline.llm.cache_hit_rate, 0)],
                      ["Tokens used", integer(data.pipeline.llm.total_tokens)],
                    ] as const
                  ).map(([label, value]) => (
                    <div
                      key={label}
                      className="flex items-baseline justify-between gap-4 border-b border-border/50 py-2 last:border-0"
                    >
                      <dt className="text-muted-foreground">{label}</dt>
                      <dd className="tabular font-medium">{value}</dd>
                    </div>
                  ))}
                </dl>
              </CardContent>
            </Card>
          </div>

          <TraceabilityCard
            sourcing={data.metrics.sourcing}
            retrieval={data.pipeline.retrieval}
          />
        </>
      ) : null}
    </div>
  );
}

/**
 * Traceability, reported separately from accuracy because it answers a different
 * question: not "did we match the human" but "can a reviewer check this".
 *
 * The reach figure is deliberately unflattering. Retrieval only covers records
 * whose manufacturer permits automated access — several large brands answer 403
 * or 429 to any non-browser client, and those are respected rather than worked
 * around. Reporting the reach makes clear which rows are evidenced and which
 * fall back to the patterns mined from the labelled set.
 */
function TraceabilityCard({
  sourcing,
  retrieval,
}: {
  sourcing: NonNullable<Metrics["sourcing"]> | undefined;
  retrieval: PipelineSummary["retrieval"] | undefined;
}) {
  if (!sourcing) return null;

  const kinds = Object.entries(sourcing.documents_by_kind ?? {});

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h3" className="flex items-center gap-2">
          <BookOpenCheck className="size-4 text-primary" aria-hidden />
          Traceability of retrieved sources
        </CardTitle>
        <CardDescription>
          Read only from manufacturers&apos; own sites and documents; marketplaces and
          distributors are refused by policy. A value counts as grounded only when it
          appears verbatim in a document that was actually fetched, so this is a floor on
          traceability rather than an estimate.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-2">
        <dl className="space-y-0 text-sm">
          {(
            [
              ["Rows with a first-party source", `${sourcing.records_with_a_source} of ${sourcing.records}`],
              ["Retrieval reach", percent(sourcing.sourced_rate, 0)],
              ["Rows supplemented by reputable third-party sources", integer(sourcing.records_supplemented_third_party ?? 0)],
              ["Verified deep product links", integer(sourcing.deep_product_links)],
              ["Documents read", integer(sourcing.documents_read)],
              ["Attribute values grounded", `${sourcing.grounded_values} of ${sourcing.filled_attribute_values}`],
              ["Grounded share of filled values", percent(sourcing.grounded_rate, 0)],
            ] as const
          ).map(([label, value]) => (
            <div
              key={label}
              className="flex items-baseline justify-between gap-4 border-b border-border/50 py-2 last:border-0"
            >
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="tabular font-medium">{value}</dd>
            </div>
          ))}
        </dl>

        <div className="space-y-4">
          {kinds.length > 0 ? (
            <div>
              <h4 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
                Documents by kind
              </h4>
              <ul className="flex flex-wrap gap-2">
                {kinds.map(([kind, count]) => (
                  <li key={kind}>
                    <Badge variant="outline">
                      {kind.replace("-", " ")} &middot; {count}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {retrieval ? (
            <div>
              <h4 className="mb-2 text-xs uppercase tracking-wide text-muted-foreground">
                Fetcher
              </h4>
              <dl className="space-y-0 text-sm">
                {(
                  [
                    ["Requests", integer(retrieval.requests)],
                    ["Served from cache", percent(retrieval.cache_hit_rate, 0)],
                    ["Unreachable or refused by host", integer(retrieval.failures)],
                    ["Blocked by sourcing policy", integer(retrieval.blocked_by_policy)],
                    ["Refused by robots.txt", integer(retrieval.robots_denied)],
                    ["Downloaded", `${retrieval.megabytes} MB`],
                  ] as const
                ).map(([label, value]) => (
                  <div
                    key={label}
                    className="flex items-baseline justify-between gap-4 border-b border-border/50 py-1.5 last:border-0"
                  >
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="tabular font-medium">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
