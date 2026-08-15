import { Suspense, lazy } from "react";
import { BarChart3, BookOpen, ClipboardCheck, Layers, Wand2 } from "lucide-react";
import { Toaster } from "sonner";

import { Header } from "@/components/layout/Header";
import { EnrichPanel } from "@/components/enrich/EnrichPanel";
import { Skeleton } from "@/components/ui/misc";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
const KnowledgePanel = lazy(() =>
  import("@/components/knowledge/KnowledgePanel").then((m) => ({
    default: m.KnowledgePanel,
  })),
);
const ReviewPanel = lazy(() =>
  import("@/components/review/ReviewPanel").then((m) => ({ default: m.ReviewPanel })),
);

const TABS = [
  { value: "enrich", label: "Enrich", icon: Wand2 },
  { value: "batch", label: "Batch", icon: Layers },
  { value: "review", label: "Review", icon: ClipboardCheck },
  { value: "evaluation", label: "Evaluation", icon: BarChart3 },
  { value: "knowledge", label: "Learned rules", icon: BookOpen },
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

  return (
    <TooltipProvider delayDuration={200}>
      <a
        href="#main"
        className="sr-only-focusable fixed left-4 top-4 z-50 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
      >
        Skip to main content
      </a>

      <Header health={health.data} loading={health.loading} error={health.error} />

      <main id="main" className="container py-8">
        <section className="mb-8 max-w-3xl">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            Turn a cryptic catalogue line into a complete product record
          </h2>
          <p className="mt-3 text-[0.9375rem] leading-relaxed text-muted-foreground">
            Distributors hand over one abbreviated string, a part number and a supplier
            name that is often a buying co-op. The delivery format wants 252 columns:
            resolved brand, taxonomy, attributes drawn from a controlled vocabulary, the
            same product described five different ways to five different length and casing
            rules, plus assets and sourcing. This pipeline closes that gap — and shows its
            work for every field.
          </p>
        </section>

        <Tabs defaultValue="enrich">
          <TabsList className="mb-2 flex-wrap">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                <tab.icon aria-hidden />
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>

          <TabsContent value="enrich">
            <EnrichPanel />
          </TabsContent>
          <TabsContent value="batch">
            <Suspense fallback={<PanelFallback />}>
              <BatchPanel />
            </Suspense>
          </TabsContent>
          <TabsContent value="review">
            <Suspense fallback={<PanelFallback />}>
              <ReviewPanel />
            </Suspense>
          </TabsContent>
          <TabsContent value="evaluation">
            <Suspense fallback={<PanelFallback />}>
              <EvaluationPanel />
            </Suspense>
          </TabsContent>
          <TabsContent value="knowledge">
            <Suspense fallback={<PanelFallback />}>
              <KnowledgePanel />
            </Suspense>
          </TabsContent>
        </Tabs>
      </main>

      <footer className="mt-12 border-t border-border/60 py-6">
        <div className="container flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
          <p>
            Local review tool — no authentication, bound to localhost. Add an auth layer
            before exposing it on a network.
          </p>
          <p>
            Scores are measured on a hash-based holdout fold the knowledge base never saw.
          </p>
        </div>
      </footer>

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
