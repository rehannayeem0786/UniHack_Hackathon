import { motion, type Variants } from "framer-motion";
import { Activity, Database, Layers3, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { CountUp } from "@/components/shared/CountUp";
import type { HealthPayload } from "@/lib/api";
import { compact } from "@/lib/format";

/**
 * The opening frame. One headline, three live numbers, and an ambient aurora
 * behind them — the product story in a single glance before any tab is opened.
 * The heading is an h2 inside main so the render smoke check can find it.
 */

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.09, delayChildren: 0.05 } },
};

const item: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { duration: 0.55, ease: [0.16, 1, 0.3, 1] } },
};

function StatChip({
  icon: Icon,
  value,
  label,
}: {
  icon: typeof Activity;
  value: string;
  label: string;
}) {
  return (
    <motion.div
      variants={item}
      className="glass card-hairline flex items-center gap-2.5 rounded-full px-4 py-2"
    >
      <Icon className="size-3.5 text-primary" aria-hidden />
      <CountUp
        value={value}
        className="font-display text-sm font-semibold leading-none"
      />
      <span className="text-xs text-muted-foreground">{label}</span>
    </motion.div>
  );
}

export function Hero({ health }: { health?: HealthPayload }) {
  const training = health?.dataset.training_rows;
  const holdout = health?.dataset.holdout_rows;
  const columns = health?.delivery_columns;

  return (
    <section className="relative overflow-hidden" aria-label="Introduction">
      {/* Ambient aurora: two slow-drifting colour fields behind the copy. */}
      <div className="pointer-events-none absolute inset-0 -z-10" aria-hidden>
        <div className="absolute -left-32 -top-40 size-[34rem] animate-drift rounded-full bg-primary/20 blur-[110px]" />
        <div className="absolute -right-24 top-10 size-[28rem] animate-drift rounded-full bg-success/15 blur-[100px] [animation-delay:-6s]" />
        <div className="absolute left-1/3 top-24 size-[20rem] animate-drift rounded-full bg-cyan-400/10 blur-[90px] [animation-delay:-12s]" />
        {/* Fine grid, faded out toward the bottom so it reads as depth. */}
        <div className="absolute inset-0 bg-grid-fade bg-[size:44px_44px] opacity-40 [mask-image:radial-gradient(ellipse_70%_60%_at_50%_0%,black,transparent)]" />
      </div>

      <motion.div
        variants={container}
        initial="hidden"
        animate="show"
        className="mx-auto max-w-3xl pb-10 pt-14 text-center sm:pt-20"
      >
        <motion.div variants={item} className="mb-5 flex justify-center">
          <Badge variant="primary" className="gap-1.5 px-3 py-1">
            <Sparkles className="size-3.5" aria-hidden />
            Multi-agent enrichment pipeline
          </Badge>
        </motion.div>

        <motion.h2
          variants={item}
          className="font-display text-balance text-4xl font-bold leading-[1.08] tracking-tight sm:text-5xl"
        >
          Turn a cryptic catalogue line into a{" "}
          <span className="text-gradient animate-gradient-x">complete product record</span>
        </motion.h2>

        <motion.p
          variants={item}
          className="mx-auto mt-5 max-w-2xl text-pretty text-[0.9375rem] leading-relaxed text-muted-foreground"
        >
          Distributors hand over one abbreviated string and a supplier name that is often a
          buying co-op. The delivery format wants {columns ?? 252} columns. Eight agents
          close that gap — and show their work for every field.
        </motion.p>

        <motion.div
          variants={item}
          className="mt-8 flex flex-wrap items-center justify-center gap-3"
        >
          <StatChip icon={Database} value={compact(training)} label="rows learned" />
          <StatChip icon={Layers3} value={compact(holdout)} label="held out for scoring" />
          <StatChip icon={Activity} value={String(columns ?? "—")} label="delivery columns" />
        </motion.div>
      </motion.div>
    </section>
  );
}
