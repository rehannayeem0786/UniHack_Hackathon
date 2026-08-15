import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Clock, Cpu, Play, ShieldCheck, Wand2, Zap } from "lucide-react";
import { toast } from "sonner";

import { MetricCard } from "@/components/shared/MetricCard";
import { PipelineFlow, STAGES, type StageStatus } from "@/components/shared/PipelineFlow";
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
import { api, type RecordPayload } from "@/lib/api";
import { percent, seconds } from "@/lib/format";

export function EnrichPanel() {
  const sample = useAsync(() => api.sample(40, "holdout"));
  const [selected, setSelected] = useState<string>();
  const [record, setRecord] = useState<RecordPayload>();
  const [status, setStatus] = useState<StageStatus>("idle");
  const [activeIndex, setActiveIndex] = useState(-1);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const [elapsed, setElapsed] = useState<number>();
  const [llmCalls, setLlmCalls] = useState<{ live: number; cached: number }>();

  const rows = sample.data?.rows ?? [];

  useEffect(() => {
    if (!selected && rows.length) setSelected(rows[0].PART_NUMBER);
  }, [rows, selected]);

  const current = useMemo(
    () => rows.find((row) => row.PART_NUMBER === selected),
    [rows, selected],
  );

  async function run() {
    if (!selected) return;
    setRunning(true);
    setError(undefined);
    setRecord(undefined);
    setStatus("active");
    setActiveIndex(0);

    // Walk the stage indicator while the request is in flight. The server does
    // not stream per-stage events for a single row, so this is presentation
    // only — the real stage timings are in the batch runs.
    const walker = window.setInterval(() => {
      setActiveIndex((index) => Math.min(index + 1, STAGES.length - 1));
    }, 320);

    try {
      const result = await api.enrich([selected]);
      const first = result.records[0];
      if (!first) throw new Error("The service returned no record.");
      setRecord(first);
      setElapsed(result.summary.elapsed_seconds);
      setLlmCalls({
        live: result.summary.llm.live_calls,
        cached: result.summary.llm.cache_hits,
      });
      setStatus("done");
      toast.success("Enrichment complete", {
        description: first.needs_review
          ? "Row completed and flagged for human review."
          : "Row completed with no outstanding issues.",
      });
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Enrichment failed.";
      setError(message);
      setStatus("idle");
      setActiveIndex(-1);
      toast.error("Enrichment failed", { description: message });
    } finally {
      window.clearInterval(walker);
      setRunning(false);
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
              Six stages, knowledge base first. Two stages never call a model, and where
              one is called it chooses from an approved list rather than writing freely.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PipelineFlow status={status} activeIndex={activeIndex} />
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
              label="Model calls"
              value={`${llmCalls?.live ?? 0} live`}
              detail={`${llmCalls?.cached ?? 0} served from cache`}
              icon={llmCalls?.live ? Cpu : Zap}
              tone={llmCalls?.live ? "default" : "success"}
              index={3}
            />
          </div>

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
