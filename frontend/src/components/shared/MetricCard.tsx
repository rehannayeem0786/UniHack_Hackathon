import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { HelpCircle } from "lucide-react";

import { CountUp } from "@/components/shared/CountUp";
import { Card } from "@/components/ui/card";
import { InfoTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export interface MetricCardProps {
  label: string;
  value: string;
  detail?: string;
  hint?: string;
  icon?: LucideIcon;
  tone?: "default" | "primary" | "success" | "warning";
  index?: number;
}

const toneRing: Record<NonNullable<MetricCardProps["tone"]>, string> = {
  default: "text-muted-foreground",
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
};

export function MetricCard({
  label,
  value,
  detail,
  hint,
  icon: Icon,
  tone = "default",
  index = 0,
}: MetricCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: Math.min(index * 0.04, 0.3), ease: [0.16, 1, 0.3, 1] }}
    >
      <Card className="card-hairline h-full p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
            {hint ? (
              <InfoTip label={hint}>
                <button
                  type="button"
                  className="text-muted-foreground/70 transition-colors hover:text-foreground"
                  aria-label={`About ${label}`}
                >
                  <HelpCircle className="size-3.5" />
                </button>
              </InfoTip>
            ) : null}
          </p>
          {Icon ? <Icon className={cn("size-4 shrink-0", toneRing[tone])} aria-hidden /> : null}
        </div>
        <CountUp
          value={value}
          className="mt-2 block font-display text-2xl font-semibold leading-none tracking-tight"
        />
        {detail ? (
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{detail}</p>
        ) : null}
      </Card>
    </motion.div>
  );
}
