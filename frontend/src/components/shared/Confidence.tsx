import { Badge } from "@/components/ui/badge";
import { InfoTip } from "@/components/ui/tooltip";
import { band, bandLabel, bandVariant, percent } from "@/lib/format";
import { cn } from "@/lib/utils";

/** A confidence score with the band that decides whether a human looks at it. */
export function ConfidenceBadge({
  value,
  label,
  className,
}: {
  value: number | undefined;
  label?: string;
  className?: string;
}) {
  const tier = band(value);
  return (
    <InfoTip label={`${bandLabel[tier]} — ${percent(value, 0)} confidence`}>
      <Badge variant={bandVariant[tier]} className={cn("tabular cursor-help", className)}>
        {label ? <span className="font-normal opacity-80">{label}</span> : null}
        {percent(value, 0)}
      </Badge>
    </InfoTip>
  );
}

/** Compact horizontal meter, for use inside dense table cells. */
export function ConfidenceMeter({ value }: { value: number | undefined }) {
  const tier = band(value);
  const width = Math.round((value ?? 0) * 100);
  const fill =
    tier === "high" ? "bg-success" : tier === "medium" ? "bg-warning" : "bg-destructive";

  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-secondary"
        role="img"
        aria-label={`${bandLabel[tier]}, ${percent(value, 0)}`}
      >
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${width}%` }} />
      </div>
      <span className="tabular text-xs text-muted-foreground">{percent(value, 0)}</span>
    </div>
  );
}
