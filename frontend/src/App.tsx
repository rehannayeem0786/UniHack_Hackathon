import { Suspense, lazy, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Banknote, BarChart3, BookOpen, ClipboardCheck, Layers, Wand2 } from "lucide-react";
import { Toaster } from "sonner";

import { Hero } from "@/components/layout/Hero";
import { Sidebar, type NavTab } from "@/components/layout/Sidebar";
import { EnrichPanel } from "@/components/enrich/EnrichPanel";
import { Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

// The panels behind the other tabs are loaded on demand. Evaluation pulls
// in the charting runtime, which is the single largest dependency here and has
// no business being in the payload of a page that may never show a chart.
const BatchPanel = lazy(() =>
  import("@/components/batch/BatchPanel").then((m) => ({ default: m.BatchPanel })),
);
const EvaluationPanel = lazy(() =>
  import("@/components/evaluation/EvaluationPanel").then((m) => ({
    default: m.EvaluationPanel,
  })),
);
const EconomicsPanel = lazy(() =>
  import("@/components/evaluation/EconomicsPanel").then((m) => ({
    default: m.EconomicsPanel,
  })),
);
const KnowledgePanel = lazy(() =>
  import("@/components/knowledge/KnowledgePanel").then((m) => ({
    default: m.KnowledgePanel,
  })),
);
const ReviewPanel = lazy(() =>
  import("@/components/review/ReviewPanel").then((m) => ({ default: m.ReviewPanel })),
);

const TABS: (NavTab & { blurb: string })[] = [
  {
    value: "enrich",
    label: "Enrich",
    icon: Wand2,
    blurb: "Run one row through all eight agents and watch them light up live.",
  },
  {
    value: "batch",
    label: "Batch",
    icon: Layers,
    blurb: "Enrich a whole fold in the background and track it to completion.",
  },
  {
    value: "review",
    label: "Review",
    icon: ClipboardCheck,
    blurb: "The human loop: approve, correct, and replay decisions.",
  },
  {
    value: "evaluation",
    label: "Evaluation",
    icon: BarChart3,
    blurb: "Field-by-field accuracy measured on rows the knowledge base never saw.",
  },
  {
    value: "economics",
    label: "Economics",
    icon: Banknote,
    blurb: "Cost per record, cache savings, and what scale does to both.",
  },
  {
    value: "knowledge",
    label: "Learned rules",
    icon: BookOpen,
    blurb: "Everything the knowledge base fitted from the labelled rows.",
  },
];

function PanelFallback() {
  return (
    <div className="space-y-4" role="status" aria-label="Loading panel">
      <div className="grid gap-5 lg:grid-cols-2">
        <Skeleton className="h-56" />
        <Skeleton className="h-56" />
      </div>
      <Skeleton className="h-40" />
    </div>
  );
}

export default function App() {
  const health = useAsync(() => api.health());
  const [active, setActive] = useState("enrich");
  const activeTab = TABS.find((tab) => tab.value === active) ?? TABS[0];

  return (
    <TooltipProvider delayDuration={200}>
      <a
        href="#main"
        className="sr-only-focusable fixed left-4 top-4 z-50 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
      >
        Skip to main content
      </a>

      {/*
        The Tabs root must wrap the sidebar too: the nav's TabsList lives in
        there, and Radix only wires triggers to content when both share a root.
      */}
      <Tabs value={active} onValueChange={setActive}>
        <div className="lg:pl-64">
          <Sidebar
            tabs={TABS}
            active={active}
            health={health.data}
            loading={health.loading}
            error={health.error}
          />

          <main id="main" className="container max-w-6xl py-6 lg:py-8">
            <Hero health={health.data} />

            {/* Per-workspace heading: swaps with the active tab. */}
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={activeTab.value}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                className="mb-6"
              >
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-primary">
                  {activeTab.label}
                </p>
                <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
                  {activeTab.blurb}
                </p>
              </motion.div>
            </AnimatePresence>

            <TabsContent value="enrich" className="mt-0">
              <EnrichPanel />
            </TabsContent>
            <TabsContent value="batch" className="mt-0">
              <Suspense fallback={<PanelFallback />}>
                <BatchPanel />
              </Suspense>
            </TabsContent>
            <TabsContent value="review" className="mt-0">
              <Suspense fallback={<PanelFallback />}>
                <ReviewPanel />
              </Suspense>
            </TabsContent>
            <TabsContent value="evaluation" className="mt-0">
              <Suspense fallback={<PanelFallback />}>
                <EvaluationPanel />
              </Suspense>
            </TabsContent>
            <TabsContent value="economics" className="mt-0">
              <Suspense fallback={<PanelFallback />}>
                <EconomicsPanel />
              </Suspense>
            </TabsContent>
            <TabsContent value="knowledge" className="mt-0">
              <Suspense fallback={<PanelFallback />}>
                <KnowledgePanel />
              </Suspense>
            </TabsContent>
          </main>

          <footer className="border-t border-border/60 py-6">
            <div className="container max-w-6xl flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
              <p>
                Local review tool — no authentication, bound to localhost. Add an auth layer
                before exposing it on a network.
              </p>
              <p>
                Scores are measured on a hash-based holdout fold the knowledge base never saw.
              </p>
            </div>
          </footer>
        </div>
      </Tabs>

      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          classNames: {
            toast:
              "!bg-popover !border-border !text-popover-foreground !shadow-lift !rounded-lg",
            description: "!text-muted-foreground",
          },
        }}
      />
    </TooltipProvider>
  );
}
