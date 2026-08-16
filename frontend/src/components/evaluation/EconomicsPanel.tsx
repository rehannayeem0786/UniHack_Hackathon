import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  Banknote,
  Calculator,
  Database,
  DollarSign,
  Globe,
  RefreshCw,
  Timer,
  Zap,
} from "lucide-react";

import { MetricCard } from "@/components/shared/MetricCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Field, Skeleton } from "@/components/ui/misc";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { compact, hoursFromSeconds, integer, percent, seconds, usd } from "@/lib/format";

/**
 * The business case on one screen: enrichment costs fractions of a cent per
 * row, and the cache makes every re-run free. The numbers come from the latest
 * holdout run — measured tokens and wall clock, not projections.
 */

const SCALE_PRESETS = [1_000, 10_000, 100_000, 1_000_000];

function CacheBar({
  label,
  hits,
  live,
  icon: Icon,
}: {
  label: string;
  hits: number;
  live: number;
  icon: typeof Zap;
}) {
  const total = hits + live;
  const rate = total ? hits / total : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-1.5 font-medium">
          <Icon className="size-3.5 text-muted-foreground" aria-hidden />
          {label}
        </span>
        <span className="tabular text-xs text-muted-foreground">
          {percent(rate, 0)} · {integer(hits)} cached / {integer(live)} live
        </span>
      </div>
      <div
        className="flex h-2.5 overflow-hidden rounded-full bg-muted/60"
        role="img"
        aria-label={`${label}: ${percent(rate, 0)} served from cache`}
      >
        <motion.div
          className="bg-success"
          initial={{ width: 0 }}
          animate={{ width: `${rate * 100}%` }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        />
        <motion.div
          className="bg-primary/70"
          initial={{ width: 0 }}
          animate={{ width: `${(1 - rate) * 100}%` }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>
    </div>
  );
}

export function EconomicsPanel() {
  const evaluation = useAsync(() => api.evaluation());
  const [rows, setRows] = useState(100_000);

  const pipeline = evaluation.data?.pipeline;
  const cost = pipeline?.cost;

  const scale = useMemo(() => {
    if (!pipeline || !cost) return undefined;
    const perRow = cost.usd_per_record_without_cache || cost.usd_per_record;
    return {
      freshCost: perRow * rows,
      rerunCost: cost.usd_per_record * rows,
      wallClock: pipeline.seconds_per_record * rows,
      perRow,
    };
  }, [pipeline, cost, rows]);

  if (evaluation.loading) {
    return (
      <div className="space-y-4" role="status" aria-label="Loading economics">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (evaluation.error || !pipeline) {
    return (
      <div className="space-y-4">
        {evaluation.error ? <ErrorState message={evaluation.error} /> : null}
        <EmptyState
          icon={Banknote}
          title="No run to price yet"
          description="Run the holdout evaluation once and the cost figures will appear here."
          action={
            <Button variant="outline" onClick={() => void evaluation.run()}>
              <RefreshCw className="size-4" aria-hidden />
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Every figure below is measured on the latest {pipeline.records}-row holdout run:
          real token counts priced at public list rates, real wall-clock seconds. Estimates
          are labelled as such; nothing here is a projection.
        </p>
        <Button variant="outline" size="sm" onClick={() => void evaluation.run()}>
          <RefreshCw className="size-4" aria-hidden />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          label="Cost per row"
          value={usd(cost?.usd_per_record)}
          detail={
            cost?.live_calls
              ? `${integer(cost.live_tokens)} tokens across ${integer(cost.live_calls)} live calls`
              : "fully cached — no live calls this run"
          }
          icon={DollarSign}
          tone="primary"
          hint="Estimated USD per enriched row, from measured token usage."
          index={0}
        />
        <MetricCard
          label="Run cost"
          value={usd(cost?.estimated_usd)}
          detail={`${pipeline.records} rows in ${seconds(pipeline.elapsed_seconds)}`}
          icon={Banknote}
          index={1}
        />
        <MetricCard
          label="Cache savings"
          value={usd(cost?.estimated_cache_savings_usd)}
          detail={`${integer(cost?.cache_hits)} responses replayed for free`}
          icon={Zap}
          tone="success"
          hint="What this run would have cost with the cache disabled."
          index={2}
        />
        <MetricCard
          label="Throughput"
          value={`${seconds(pipeline.seconds_per_record)}/row`}
          detail={`${compact(pipeline.records / Math.max(pipeline.elapsed_seconds, 0.001))} rows per second, concurrent`}
          icon={Timer}
          index={3}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Zap className="size-4 text-success" aria-hidden />
              Cache economics
            </CardTitle>
            <CardDescription className="text-xs">
              Both the model gateway and the web fetcher cache every response on disk.
              A second run of the same catalogue costs nothing and needs no network.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <CacheBar
              label="LLM responses"
              hits={pipeline.llm.cache_hits}
              live={pipeline.llm.live_calls}
              icon={Zap}
            />
            <CacheBar
              label="Web documents"
              hits={pipeline.retrieval.cache_hits}
              live={pipeline.retrieval.live_requests}
              icon={Globe}
            />

            {Object.keys(pipeline.llm.by_model).length ? (
              <div className="space-y-1.5 border-t border-border/50 pt-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Model mix this run
                </p>
                <ul className="space-y-1">
                  {Object.entries(pipeline.llm.by_model).map(([model, calls]) => (
                    <li key={model} className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate font-mono text-xs">{model}</span>
                      <span className="tabular text-xs text-muted-foreground">
                        {integer(calls)} calls
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Calculator className="size-4 text-primary" aria-hidden />
              What would a full catalogue cost?
            </CardTitle>
            <CardDescription className="text-xs">
              Scale the measured per-row numbers to any catalogue size. First run is the
              worst case; every re-run is served from cache.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Catalogue rows">
              <div className="flex flex-wrap gap-2">
                {SCALE_PRESETS.map((preset) => (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setRows(preset)}
                    className={
                      rows === preset
                        ? "rounded-md border border-primary/50 bg-primary/12 px-3 py-1.5 text-sm font-medium text-primary"
                        : "rounded-md border border-border bg-surface/40 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-primary/30 hover:text-foreground"
                    }
                    aria-pressed={rows === preset}
                  >
                    {compact(preset)}
                  </button>
                ))}
              </div>
            </Field>

            {scale ? (
              <dl className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-lg border border-border/60 bg-surface/40 p-3">
                  <dt className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                    First run
                  </dt>
                  <dd className="tabular mt-1 text-xl font-semibold">{usd(scale.freshCost)}</dd>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {usd(scale.perRow)} per row
                  </p>
                </div>
                <div className="rounded-lg border border-success/25 bg-success/[0.05] p-3">
                  <dt className="text-[0.6875rem] uppercase tracking-wide text-success">
                    Re-run (cached)
                  </dt>
                  <dd className="tabular mt-1 text-xl font-semibold text-success">
                    {usd(scale.rerunCost)}
                  </dd>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {cost?.cache_hits ? "cache replays every response" : "nothing to re-fetch"}
                  </p>
                </div>
                <div className="rounded-lg border border-border/60 bg-surface/40 p-3">
                  <dt className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                    Wall clock
                  </dt>
                  <dd className="tabular mt-1 text-xl font-semibold">
                    {hoursFromSeconds(scale.wallClock)}
                  </dd>
                  <p className="mt-1 text-xs text-muted-foreground">
                    at {seconds(pipeline.seconds_per_record)} per row
                  </p>
                </div>
              </dl>
            ) : null}

            <p className="text-xs leading-relaxed text-muted-foreground">
              At these rates, enriching a {compact(rows)}-row catalogue costs about{" "}
              <span className="font-medium text-foreground">
                {scale ? usd(scale.freshCost) : "—"}
              </span>{" "}
              once — and pennies thereafter. That is the viability story: this is not a
              demo that breaks the budget at production scale.
            </p>
          </CardContent>
        </Card>
      </div>

      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Database className="size-3.5" aria-hidden />
        Figures are estimates from measured usage at public list prices;{" "}
        {cost?.basis ?? "cached calls cost nothing"}.
      </p>
    </div>
  );
}

