import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Banknote,
  CheckCircle2,
  Database,
  Download,
  FileUp,
  Gauge,
  Layers,
  Play,
  Target,
  Timer,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { MetricCard } from "@/components/shared/MetricCard";
import { ConfidenceMeter } from "@/components/shared/Confidence";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Field, Skeleton } from "@/components/ui/misc";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePoll } from "@/hooks/useAsync";
import {
  api,
  type JobState,
  type Metrics,
  type PipelineSummary,
  type RecordPayload,
} from "@/lib/api";
import { percent, seconds, usd } from "@/lib/format";

const FOLDS = [
  { value: "holdout", label: "Holdout — unseen by the knowledge base", hint: "The honest number" },
  { value: "train", label: "Train — rows the registries were fitted on", hint: "Optimistic" },
  { value: "all", label: "All 200 labelled rows", hint: "Mixed" },
];

export function BatchPanel() {
  const [jobId, setJobId] = useState<string>();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();
  const [fold, setFold] = useState("holdout");
  const [limit, setLimit] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [records, setRecords] = useState<RecordPayload[]>();
  const [summary, setSummary] = useState<PipelineSummary>();
  const [metrics, setMetrics] = useState<Metrics>();

  const active = Boolean(jobId);
  const polled = usePoll<JobState>(() => api.job(jobId as string), active && !records, {
    key: jobId,
  });

  // Only trust a poll that belongs to the job currently on screen.
  const job = polled && polled.id === jobId ? polled : undefined;
  const finished = job?.status === "done";

  useEffect(() => {
    if (!finished || !jobId || records) return;
    let cancelled = false;

    void (async () => {
      try {
        const page = await api.jobResults(jobId, 0, 100);
        if (cancelled) return;

        // Scored only when the fold carried ground truth; an upload has none,
        // and a missing score is not an error.
        const scored = job?.has_metrics
          ? await api.jobMetrics(jobId).catch(() => undefined)
          : undefined;
        if (cancelled) return;

        // Every piece of state is set together, after both requests. Setting
        // `records` earlier re-runs this effect — `records` is one of its
        // dependencies — and the cleanup would cancel the metrics fetch that had
        // already succeeded.
        setRecords(page.records);
        setSummary(page.summary);
        setMetrics(scored);
        toast.success("Run complete", {
          description: `${page.total} rows enriched. The delivery workbook is ready to download.`,
        });
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Could not load results.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [finished, jobId, records]);

  useEffect(() => {
    if (job?.status === "failed" && job.error) {
      setError(job.error);
      toast.error("Run failed", { description: job.error });
    }
  }, [job?.status, job?.error]);

  function reset() {
    setJobId(undefined);
    setRecords(undefined);
    setSummary(undefined);
    setMetrics(undefined);
    setError(undefined);
  }

  async function startDataset() {
    reset();
    setStarting(true);
    try {
      const parsed = limit.trim() ? Number.parseInt(limit, 10) : undefined;
      const started = await api.startDatasetJob(
        fold,
        Number.isFinite(parsed) ? parsed : undefined,
      );
      setJobId(started.id);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Could not start the run.";
      setError(message);
      toast.error("Could not start", { description: message });
    } finally {
      setStarting(false);
    }
  }

  async function startUpload() {
    if (!file) {
      toast.error("Choose a file first");
      return;
    }
    reset();
    setStarting(true);
    try {
      const started = await api.uploadJob(file);
      setJobId(started.id);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Upload failed.";
      setError(message);
      toast.error("Upload failed", { description: message });
    } finally {
      setStarting(false);
    }
  }

  const progressValue = job ? Math.round(job.progress * 100) : 0;

  return (
    <div className="space-y-6">
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle as="h2" className="flex items-center gap-2">
              <Database className="size-4 text-primary" aria-hidden />
              Run the bundled dataset
            </CardTitle>
            <CardDescription>
              Enrich a fold of the 200 labelled rows. Where labels exist the run is scored
              against them, so the accuracy figures come from the same code path the
              demo uses.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Fold">
              <Select value={fold} onValueChange={setFold}>
                <SelectTrigger aria-label="Select a dataset fold">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FOLDS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Row limit" hint="Leave blank to run the whole fold.">
              <input
                type="number"
                min={1}
                max={200}
                value={limit}
                onChange={(event) => setLimit(event.target.value)}
                placeholder="all"
                className="tabular h-9 w-full rounded-md border border-input bg-surface px-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
              />
            </Field>
            <Button onClick={() => void startDataset()} loading={starting} className="w-full">
              <Play className="size-4" aria-hidden />
              Start run
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h2" className="flex items-center gap-2">
              <FileUp className="size-4 text-primary" aria-hidden />
              Upload your own catalogue
            </CardTitle>
            <CardDescription>
              CSV or Excel containing a <code className="rounded bg-secondary px-1 py-0.5 text-xs">PART_NUMBER</code>{" "}
              column. With no ground truth you still get the full delivery workbook and
              rule-compliance figures, just no accuracy score.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="File" hint="Same column shape as the bundled input sheet.">
              <input
                type="file"
                accept=".csv,.xlsx,.xls"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="w-full cursor-pointer rounded-md border border-input bg-surface px-3 py-2 text-sm file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1 file:text-xs file:font-medium file:text-secondary-foreground hover:border-primary/40 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 focus:ring-offset-background"
              />
            </Field>
            <Button
              onClick={() => void startUpload()}
              loading={starting}
              variant="secondary"
              className="w-full"
            >
              <FileUp className="size-4" aria-hidden />
              Upload and enrich
            </Button>
          </CardContent>
        </Card>
      </div>

      {error ? <ErrorState message={error} action={<Button size="sm" variant="secondary" onClick={reset}>Dismiss</Button>} /> : null}

      {job ? (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <CardTitle as="h2">{job.label}</CardTitle>
                <CardDescription>
                  Job <code className="font-mono text-xs">{job.id}</code> · started{" "}
                  {new Date(job.created_at).toLocaleTimeString()}
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Badge
                  variant={
                    job.status === "done"
                      ? "success"
                      : job.status === "failed"
                        ? "destructive"
                        : "primary"
                  }
                >
                  {job.status === "done" ? <CheckCircle2 aria-hidden /> : null}
                  {job.status}
                </Badge>
                {finished ? (
                  <Button size="sm" variant="secondary" asChild>
                    <a href={api.exportUrl(job.id)} download>
                      <Download className="size-4" aria-hidden />
                      Delivery workbook
                    </a>
                  </Button>
                ) : null}
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Progress value={progressValue} aria-label="Rows enriched" />
              <p className="tabular text-xs text-muted-foreground">
                {job.done} of {job.total} rows · {progressValue}%
              </p>
            </div>

            {summary ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
                <MetricCard
                  label="Rows"
                  value={String(summary.records)}
                  detail={`${seconds(summary.seconds_per_record)} per row`}
                  icon={Layers}
                  tone="primary"
                  index={0}
                />
                <MetricCard
                  label="Mean confidence"
                  value={percent(summary.mean_confidence, 1)}
                  detail={`${summary.needs_review} rows flagged for review`}
                  icon={Gauge}
                  tone={summary.mean_confidence >= 0.7 ? "success" : "warning"}
                  index={1}
                />
                <MetricCard
                  label="Wall clock"
                  value={seconds(summary.elapsed_seconds)}
                  detail="Concurrent across rows"
                  icon={Timer}
                  index={2}
                />
                <MetricCard
                  label="Cache hit rate"
                  value={percent(summary.llm.cache_hit_rate, 0)}
                  detail={`${summary.llm.live_calls} live calls, ${summary.llm.total_tokens.toLocaleString()} tokens`}
                  icon={Zap}
                  tone="success"
                  index={3}
                />
                <MetricCard
                  label="Estimated cost"
                  value={usd(summary.cost?.estimated_usd)}
                  detail={
                    summary.cost?.usd_per_record !== undefined
                      ? `${usd(summary.cost.usd_per_record)} per row`
                      : "from measured token usage"
                  }
                  icon={Banknote}
                  tone={summary.cost?.estimated_usd ? "default" : "success"}
                  hint="Estimated USD at public list prices. Cached responses cost nothing."
                  index={4}
                />
              </div>
            ) : null}

            {metrics ? (
              <div className="rounded-lg border border-border/60 bg-surface/40 p-4">
                <p className="mb-3 flex flex-wrap items-center gap-2 text-sm font-medium">
                  <Target className="size-4 text-primary" aria-hidden />
                  Scored against ground truth
                  {job.label === "dataset:train" ? (
                    <Badge variant="warning" className="text-[0.625rem]">
                      training fold — the registries saw these rows
                    </Badge>
                  ) : job.label === "dataset:all" ? (
                    <Badge variant="warning" className="text-[0.625rem]">
                      mixed fold — includes fitted rows
                    </Badge>
                  ) : (
                    <Badge variant="success" className="text-[0.625rem]">
                      held-out fold
                    </Badge>
                  )}
                </p>
                <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  {(
                    [
                      ["Mean exact match", percent(metrics.headline.mean_exact_match, 1)],
                      ["Mean fuzzy match", percent(metrics.headline.mean_fuzzy_match, 1)],
                      ["Schema coverage", percent(metrics.coverage.fill_ratio_vs_truth, 1)],
                      ["Attribute label precision", percent(metrics.attributes.label_precision, 1)],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label}>
                      <dt className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                        {label}
                      </dt>
                      <dd className="tabular mt-0.5 text-xl font-semibold">{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : null}

            {records?.length ? (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                <Table caption="Enriched rows from this run">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[12%]">Part</TableHead>
                      <TableHead className="w-[16%]">Brand</TableHead>
                      <TableHead className="w-[24%]">Classpath</TableHead>
                      <TableHead className="w-[34%]">Search description</TableHead>
                      <TableHead className="w-[14%]">Confidence</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((record) => (
                      <TableRow key={record.part_number}>
                        <TableCell className="font-mono text-xs">{record.part_number}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5">
                            <span className="truncate">{record.output.brand_name ?? "—"}</span>
                            {record.needs_review ? (
                              <Badge variant="warning" className="shrink-0 text-[0.5625rem]">
                                review
                              </Badge>
                            ) : null}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {record.output.classpath?.split(">").pop()?.trim() ?? "—"}
                        </TableCell>
                        <TableCell className="text-xs leading-relaxed">
                          {record.output.short_desc ?? "—"}
                        </TableCell>
                        <TableCell>
                          <ConfidenceMeter value={record.confidence.overall} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {records.length < job.total ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Showing the first {records.length} of {job.total} rows. Download the
                    workbook for the complete 252-column output.
                  </p>
                ) : null}
              </motion.div>
            ) : job.status === "running" || job.status === "queued" ? (
              <div className="space-y-2">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={index} className="h-9 w-full" />
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : (
        <EmptyState
          icon={Database}
          title="No run in progress"
          description="Start a dataset fold or upload a catalogue. Progress is reported per row and the result downloads as the 252-column delivery workbook."
        />
      )}
    </div>
  );
}
