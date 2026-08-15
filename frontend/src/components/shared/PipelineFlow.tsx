import { motion } from "framer-motion";
import {
  Check,
  FileText,
  Factory,
  Image,
  Loader2,
  ShieldCheck,
  Sparkles,
  Tags,
  type LucideIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Stage {
  key: string;
  title: string;
  detail: string;
  icon: LucideIcon;
  model: "none" | "picks" | "extracts" | "writes";
}

/**
 * The six stages, in order, with an honest note on what the model is allowed to
 * do in each. Three of the six never call a model at all, which is the point:
 * generation is constrained by lookups rather than trusted to behave.
 */
export const STAGES: Stage[] = [
  {
    key: "classifier",
    title: "Classify",
    detail:
      "Category triple narrows 62 classpaths to a shortlist. A near-duplicate labelled row decides it outright; otherwise the model picks from the shortlist.",
    icon: Tags,
    model: "picks",
  },
  {
    key: "manufacturer_resolver",
    title: "Resolve brand",
    detail:
      "Brand hints, then part-number prefix, then description shorthand. A buying co-op supplier string is ignored, because it fronts six unrelated brands.",
    icon: Factory,
    model: "picks",
  },
  {
    key: "attribute_extractor",
    title: "Extract attributes",
    detail:
      "The classpath template fixes which attributes exist and in what order. Where a controlled vocabulary exists, answers are snapped onto it.",
    icon: Sparkles,
    model: "extracts",
  },
  {
    key: "description_builder",
    title: "Build descriptions",
    detail:
      "Five surfaces assembled by a grammar mined from labelled rows. Values come only from attributes that already passed validation.",
    icon: FileText,
    model: "writes",
  },
  {
    key: "sourcing",
    title: "Assets & sourcing",
    detail:
      "Asset filenames from the house naming convention, plus the approved manufacturer domain. Fully deterministic — no model call.",
    icon: Image,
    model: "none",
  },
  {
    key: "validator",
    title: "Validate & score",
    detail:
      "Character limits, casing, unit spacing, fractions and vocabulary membership. Assigns per-field confidence and the needs-review flag.",
    icon: ShieldCheck,
    model: "none",
  },
];

const MODEL_BADGE: Record<Stage["model"], { text: string; variant: "outline" | "primary" }> = {
  none: { text: "Deterministic", variant: "outline" },
  picks: { text: "Model picks", variant: "primary" },
  extracts: { text: "Model extracts", variant: "primary" },
  writes: { text: "Formula", variant: "outline" },
};

export type StageStatus = "idle" | "active" | "done";

export function PipelineFlow({
  status = "idle",
  activeIndex = -1,
}: {
  status?: StageStatus;
  activeIndex?: number;
}) {
  return (
    <ol className="space-y-2" aria-label="Enrichment pipeline stages">
      {STAGES.map((stage, index) => {
        const isDone = status === "done" || (status === "active" && index < activeIndex);
        const isActive = status === "active" && index === activeIndex;
        const badge = MODEL_BADGE[stage.model];

        return (
          <motion.li
            key={stage.key}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: index * 0.05 }}
            className={cn(
              "flex gap-3 rounded-lg border p-3 transition-colors",
              isActive
                ? "border-primary/50 bg-primary/[0.07]"
                : isDone
                  ? "border-success/25 bg-success/[0.04]"
                  : "border-border/60 bg-surface/40",
            )}
          >
            <div
              className={cn(
                "mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border",
                isDone
                  ? "border-success/40 bg-success/15 text-success"
                  : isActive
                    ? "animate-pulse-ring border-primary/50 bg-primary/15 text-primary"
                    : "border-border bg-secondary text-muted-foreground",
              )}
              aria-hidden
            >
              {isDone ? (
                <Check className="size-3.5" />
              ) : isActive ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <stage.icon className="size-3.5" />
              )}
            </div>

            <div className="min-w-0 space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-medium leading-none">
                  <span className="tabular mr-1.5 text-muted-foreground">{index + 1}.</span>
                  {stage.title}
                </p>
                <Badge variant={badge.variant} className="text-[0.625rem]">
                  {badge.text}
                </Badge>
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">{stage.detail}</p>
            </div>
          </motion.li>
        );
      })}
    </ol>
  );
}
