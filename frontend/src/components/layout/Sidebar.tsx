import { motion } from "framer-motion";
import { Activity, BookText, Boxes, CircleAlert, Loader2, type LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InfoTip } from "@/components/ui/tooltip";
import type { HealthPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface NavTab {
  value: string;
  label: string;
  icon: LucideIcon;
}

function StatusPill({
  health,
  loading,
  error,
  compact,
}: {
  health?: HealthPayload;
  loading: boolean;
  error?: string;
  compact?: boolean;
}) {
  if (loading) {
    return (
      <Badge variant="outline" className="gap-2">
        <Loader2 className="animate-spin" aria-hidden />
        {compact ? null : "Connecting"}
      </Badge>
    );
  }

  if (error || !health) {
    return (
      <InfoTip label={error ?? "The service did not respond."}>
        <Badge variant="destructive" className="cursor-help gap-2">
          <CircleAlert aria-hidden />
          {compact ? null : "Offline"}
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
        {compact ? null : `Live · ${providers}`}
      </Badge>
    </InfoTip>
  );
}

/**
 * The command rail. On desktop it is a fixed left column — brand, the six
 * workspaces, and the live service status stacked top to bottom. Below `lg`
 * it collapses into a sticky top bar with a horizontally scrolling nav, so the
 * same Radix tablist drives both without duplicating any state.
 */
export function Sidebar({
  tabs,
  active,
  health,
  loading,
  error,
}: {
  tabs: NavTab[];
  active: string;
  health?: HealthPayload;
  loading: boolean;
  error?: string;
}) {
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-xl lg:fixed lg:inset-y-0 lg:left-0 lg:w-64 lg:border-b-0 lg:border-r lg:bg-background/60">
      <div className="flex h-full flex-col">
        {/* Brand */}
        <div className="flex items-center justify-between gap-3 px-4 py-4 lg:px-5 lg:py-6">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary/50 text-primary-foreground shadow-glow"
              aria-hidden
            >
              <Boxes className="size-5" />
            </span>
            <div className="min-w-0">
              <h1 className="font-display truncate text-[0.9375rem] font-semibold leading-tight tracking-tight">
                Product Intelligence
              </h1>
              <p className="hidden truncate text-xs text-muted-foreground lg:block">
                Multi-agent enrichment
              </p>
            </div>
          </div>
          {/* Compact live dot for the collapsed mobile bar. */}
          <div className="lg:hidden">
            <StatusPill health={health} loading={loading} error={error} compact />
          </div>
        </div>

        {/* Navigation */}
        <nav className="px-3 lg:flex-1 lg:overflow-y-auto" aria-label="Workspaces">
          <TabsList
            className="flex h-auto flex-row gap-1 overflow-x-auto border-0 bg-transparent p-0 backdrop-blur-0 lg:flex-col lg:overflow-visible"
          >
            {tabs.map((tab) => {
              const isActive = active === tab.value;
              return (
                <TabsTrigger
                  key={tab.value}
                  value={tab.value}
                  className={cn(
                    "group relative w-auto shrink-0 items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium lg:w-full",
                    "justify-start text-muted-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none",
                    "hover:text-foreground",
                  )}
                >
                  {isActive ? (
                    <motion.span
                      layoutId="nav-pill"
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                      className="absolute inset-0 rounded-lg border border-primary/25 bg-primary/[0.10] shadow-glow"
                      aria-hidden
                    />
                  ) : null}
                  <tab.icon
                    className={cn(
                      "relative z-10 size-4 shrink-0 transition-colors",
                      isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground",
                    )}
                    aria-hidden
                  />
                  <span className="relative z-10 whitespace-nowrap">{tab.label}</span>
                </TabsTrigger>
              );
            })}
          </TabsList>
        </nav>

        {/* Service status — desktop only; mobile shows the compact dot above. */}
        <div className="hidden space-y-3 border-t border-border/60 px-5 py-5 lg:block">
          {health ? (
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <Activity className="size-3.5" aria-hidden />
                Delivery format
              </span>
              <span className="tabular font-medium text-foreground">
                {health.delivery_columns} cols
              </span>
            </div>
          ) : null}
          <StatusPill health={health} loading={loading} error={error} />
          <Button variant="ghost" size="sm" asChild className="w-full justify-start px-2">
            <a href="/docs" target="_blank" rel="noreferrer noopener">
              <BookText className="size-4" aria-hidden />
              API reference
            </a>
          </Button>
        </div>
      </div>
    </header>
  );
}
