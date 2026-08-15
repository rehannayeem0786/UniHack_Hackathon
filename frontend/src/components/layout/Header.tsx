import { Activity, BookText, Boxes, CircleAlert, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InfoTip } from "@/components/ui/tooltip";
import type { HealthPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

function StatusPill({
  health,
  loading,
  error,
}: {
  health?: HealthPayload;
  loading: boolean;
  error?: string;
}) {
  if (loading) {
    return (
      <Badge variant="outline" className="gap-2">
        <Loader2 className="animate-spin" aria-hidden />
        Connecting
      </Badge>
    );
  }

  if (error || !health) {
    return (
      <InfoTip label={error ?? "The service did not respond."}>
        <Badge variant="destructive" className="cursor-help gap-2">
          <CircleAlert aria-hidden />
          Offline
        </Badge>
      </InfoTip>
    );
  }

  const providers = health.llm.providers.join(" → ") || "none";
  return (
    <InfoTip
      label={
        <span>
          Model providers: {providers}. Knowledge base fitted on{" "}
          {health.dataset.training_rows} training rows; {health.dataset.holdout_rows} rows
          held out for scoring.
        </span>
      }
    >
      <Badge variant="success" className="cursor-help gap-2">
        <span className="relative flex size-1.5" aria-hidden>
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-success opacity-70" />
          <span className="relative inline-flex size-1.5 rounded-full bg-success" />
        </span>
        Live · {providers}
      </Badge>
    </InfoTip>
  );
}

export function Header({
  health,
  loading,
  error,
}: {
  health?: HealthPayload;
  loading: boolean;
  error?: string;
}) {
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-xl">
      <div className="container flex h-16 items-center justify-between gap-4">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary/50 text-primary-foreground shadow-glow"
            aria-hidden
          >
            <Boxes className="size-5" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate text-[0.9375rem] font-semibold leading-tight tracking-tight">
              Product Intelligence
            </h1>
            <p className="truncate text-xs text-muted-foreground">
              Multi-agent enrichment for industrial catalogues
            </p>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {health ? (
            <InfoTip label="Columns in the delivery format this pipeline writes.">
              <Badge variant="outline" className="tabular hidden cursor-help gap-1.5 sm:flex">
                <Activity aria-hidden />
                {health.delivery_columns} columns
              </Badge>
            </InfoTip>
          ) : null}
          <StatusPill health={health} loading={loading} error={error} />
          <Button variant="ghost" size="sm" asChild>
            <a href="/docs" target="_blank" rel="noreferrer noopener">
              <BookText className="size-4" aria-hidden />
              <span className={cn("hidden sm:inline")}>API</span>
            </a>
          </Button>
        </div>
      </div>
    </header>
  );
}
