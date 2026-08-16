import { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Clock, DollarSign, Play, ShieldCheck, Wand2, Zap } from "lucide-react";
import { toast } from "sonner";

import { MetricCard } from "@/components/shared/MetricCard";
import { PipelineFlow, type StageState, type StageStatus } from "@/components/shared/PipelineFlow";
import { BeforeAfter } from "@/components/enrich/BeforeAfter";
import { RecordView } from "@/components/enrich/RecordView";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Field, Skeleton } from "@/components/ui/misc";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useAsync } from "@/hooks/useAsync";
import { api, type PipelineSummary, type RecordPayload } from "@/lib/api";
import { percent, seconds, usd } from "@/lib/format";

export function EnrichPanel() {
  const sample = useAsync(() => api.sample(40, "holdout"));
  const [selected, setSelected] = useState<string>();
  const [record, setRecord] = useState<RecordPayload>();
  const [status, setStatus] = useState<StageStatus>("idle");
  const [stageStates, setStageStates] = useState<Record<string, StageState>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const [elapsed, setElapsed] = useState<number>();
  const [summary, setSummary] = useState<PipelineSummary>();
  const abortRef = useRef<AbortController>();

  const rows = sample.data?.rows ?? [];

  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0].PART_NUMBER);
  }, [rows, selected]);

  // Abort an in-flight stream when the panel unmounts, so a late event can
  // never update state that no longer exists.
  useEffect(() => () => abortRef.current?.abort(), []);

  const current = useMemo(
    () => rows.find((row) => row.PART_NUMBER === selected),
    [rows, selected],
  );

  async function run() {
    if (!selected) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setRunning(true);
    setError(undefined);
    setRecord(undefined);
    setSummary(undefined);
    setStatus("active");
    setStageStates({});

    try {
      // Real stage events, streamed from the server as each agent starts and
      // finishes — no timers, no guessing. The eight agents light up live.
      let streamed: RecordPayload | undefined;
      await api.enrichStream(
        selected,
        (event) => {
          if (event.type === "stage") {
            setStageStates((prev) => ({
              ...prev,
              [event.stage]:
                event.event === "start"
                  ? { status: "active" }
                  : { status: "done", elapsedMs: event.elapsed_ms },
            }));
          } else if (event.type === "record") {
            streamed = event.record;
            setRecord(event.record);
          } else if (event.type === "summary") {
            setSummary(event.summary);
            setElapsed(event.summary.elapsed_seconds);
          }
        },
        controller.signal,
      );

      setStatus("done");
      toast.success("Enrichment complete", {
        description: streamed?.needs_review
          ? "Row completed and flagged for human review."
          : "Row completed with no outstanding issues.",
      });
    } catch (caught) {
      if (controller.signal.aborted) return;
      const message = caught instanceof Error ? caught.message : "Enrichment failed.";
      setError(message);
      setStatus("idle");
      setStageStates({});
      toast.error("Enrichment failed", { description: message });
    } finally {
      if (abortRef.current === controller) {
        setRunning(false);
      }
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle as="h2">Pick a raw catalogue row</CardTitle>
            <CardDescription>
              These rows come from the held-out fold — the knowledge base was never
              fitted on them, so nothing you see below is a row the pipeline has
              memorised.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {sample.loading ? (
              <div className="space-y-3">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : sample.error ? (
              <ErrorState
                message={sample.error}
                action={
                  <Button size="sm" variant="secondary" onClick={() => void sample.run()}>
                    Retry
                  </Button>
                }
              />
            ) : (
              <>
                <Field label="Sample row" hint={`${rows.length} held-out rows available`}>
                  <Select value={selected} onValueChange={setSelected}>
                    <SelectTrigger aria-label="Select a sample row">
                      <SelectValue placeholder="Choose a row" />
                    </SelectTrigger>
                    <SelectContent className="max-h-80">
                      {rows.map((row) => (
                        <SelectItem key={row.PART_NUMBER} value={row.PART_NUMBER}>
                          {row.Part_Desc || row.PART_NUMBER}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                {current ? (
                  <dl className="grid gap-x-4 gap-y-2 rounded-md border border-border/60 bg-surface/50 p-3 text-sm sm:grid-cols-2">
                    {(
                      [
                        ["Description", current.Part_Desc],
                        ["Mfg part number", current.Mfg_Part_Num],
                        ["Supplier", current.Part_Manuf],
                        ["Category", [current.Dept, current.Class, current.Fine].filter(Boolean).join(" › ")],
                      ] as const
                    ).map(([label, value]) => (
                      <div key={label} className="min-w-0">
                        <dt className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                          {label}
                        </dt>
                        <dd className="truncate font-mono text-[0.78125rem]" title={value}>
                          {value || "—"}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : null}

                <Button onClick={() => void run()} loading={running} size="lg" className="w-full">
                  {!running ? <Play className="size-4" aria-hidden /> : null}
                  {running ? "Enriching…" : "Run enrichment pipeline"}
                </Button>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h2">Pipeline</CardTitle>
            <CardDescription>
              Eight agents, knowledge base first. Four never call a model, and where one
              is called it chooses from an approved list rather than writing freely.
              Watch them light up in real time as the row is enriched.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PipelineFlow status={status} stageStates={stageStates} />
          </CardContent>
        </Card>
      </div>

      {error ? <ErrorState message={error} /> : null}

      {record ? (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
          className="space-y-6"
        >
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <MetricCard
              label="Overall confidence"
              value={percent(record.confidence.overall, 0)}
              detail={record.needs_review ? "Flagged for human review" : "Above the review threshold"}
              icon={ShieldCheck}
              tone={record.needs_review ? "warning" : "success"}
              index={0}
            />
            <MetricCard
              label="Attributes filled"
              value={`${record.attributes.filter((a) => a.value).length} / ${record.attributes.length}`}
              detail="Template labels populated"
              icon={Wand2}
              tone="primary"
              index={1}
            />
            <MetricCard
              label="Wall clock"
              value={seconds(elapsed)}
              detail="End to end for this row"
              icon={Clock}
              index={2}
            />
            <MetricCard
              label="Estimated cost"
              value={usd(summary?.cost?.estimated_usd)}
              detail={
                summary?.cost
                  ? summary.cost.live_calls
                    ? `${summary.cost.live_calls} live calls · ${summary.cost.cache_hits} cached`
                    : "fully served from cache — $0"
                  : `${summary?.llm.live_calls ?? 0} live calls`
              }
              icon={summary?.cost?.estimated_usd ? DollarSign : Zap}
              tone={summary?.cost?.estimated_usd ? "default" : "success"}
              hint="Estimated from token counts at public list prices. Cached responses cost nothing."
              index={3}
            />
          </div>

          <BeforeAfter record={record} />

          <RecordView record={record} />
        </motion.div>
      ) : !running && !error ? (
        <EmptyState
          icon={Wand2}
          title="No record enriched yet"
          description="Choose a row above and run the pipeline to see the full 252-column record, the five description surfaces and the provenance behind every field."
        />
      ) : null}
    </div>
  );
}
