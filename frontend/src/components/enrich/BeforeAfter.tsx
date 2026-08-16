import { motion } from "framer-motion";
import { ArrowRight, CheckCircle2, CircleDashed, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { RecordPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * The value proposition in one glance: the raw catalogue row the distributor
 * handed over, next to the enriched record the pipeline produced. No
 * explanation needed — the empty cells on the left and the filled, sourced
 * cells on the right tell the story in three seconds.
 */

interface Row {
  label: string;
  before: string;
  after: string;
  /** A checkmark-worthy note on the after cell, e.g. the source domain. */
  note?: string;
  noteUrl?: string;
  mono?: boolean;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Short, lowercase document-type names for the Source row, e.g. "datasheet". */
const KIND_SHORT: Record<string, string> = {
  specification: "datasheet",
  manual: "manual",
  "product-page": "product page",
  "support-page": "support page",
  other: "document",
};

export function BeforeAfter({ record }: { record: RecordPayload }) {
  const { input, output, citations } = record;

  const firstSource = citations.find((c) => c.kind !== "other") ?? citations[0];

  // When research retrieved no document, fall back to the approved
  // manufacturer domain that the sourcing stage resolved.
  const fallbackUrl = output.mfr_url ?? "";
  const sourceUrl = firstSource?.url ?? fallbackUrl;
  const sourceLabel = firstSource
    ? `${hostOf(firstSource.url)} ${KIND_SHORT[firstSource.kind] ?? firstSource.kind}`
    : fallbackUrl
      ? `${hostOf(fallbackUrl)} manufacturer site`
      : "";
  const sourceNote = firstSource
    ? `verified on ${hostOf(firstSource.url)}`
    : fallbackUrl
      ? "approved manufacturer domain"
      : undefined;

  const rows: Row[] = [
    {
      label: "Part number",
      before: record.part_number,
      after: output.mpn ?? record.part_number,
      mono: true,
    },
    {
      label: "Brand",
      before: input.brand_hints.filter(Boolean).join(", "),
      after: output.brand_name ?? "",
    },
    {
      label: "Manufacturer",
      before: input.supplier,
      after: output.manufacturer_name ?? "",
    },
    {
      label: "Category",
      before: [input.dept, input.class, input.fine].filter(Boolean).join(" › "),
      after: output.classpath?.split(">").pop()?.trim() ?? "",
    },
    {
      label: "Description",
      before: input.description,
      after: output.short_desc ?? output.long_desc ?? "",
    },
    {
      label: "Source",
      before: "",
      after: sourceLabel,
      note: sourceNote,
      noteUrl: sourceUrl || undefined,
    },
  ];

  const filledAfter = rows.filter((r) => r.after.trim()).length;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle as="h3" className="flex items-center gap-2">
              Before
              <ArrowRight className="size-4 text-muted-foreground" aria-hidden />
              After
            </CardTitle>
            <CardDescription>
              What the distributor handed over, and what the pipeline returns.
            </CardDescription>
          </div>
          <Badge variant="success" className="tabular">
            {filledAfter} / {rows.length} fields filled
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-hidden rounded-lg border border-border/60">
          {/* Column headers */}
          <div className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)_minmax(0,1.6fr)] border-b border-border/60 bg-surface/60 text-[0.6875rem] font-medium uppercase tracking-wide text-muted-foreground">
            <div className="px-3 py-2">Field</div>
            <div className="border-l border-border/60 px-3 py-2">Before · raw input</div>
            <div className="border-l border-border/60 px-3 py-2 text-success">
              After · enriched
            </div>
          </div>

          {rows.map((row, index) => {
            const added = !row.before.trim() && row.after.trim();
            const changed =
              row.before.trim() &&
              row.after.trim() &&
              row.before.trim().toLowerCase() !== row.after.trim().toLowerCase();

            return (
              <motion.div
                key={row.label}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25, delay: index * 0.06 }}
                className={cn(
                  "grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.4fr)_minmax(0,1.6fr)] text-sm",
                  index < rows.length - 1 && "border-b border-border/40",
                )}
              >
                <div className="px-3 py-2.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {row.label}
                </div>

                <div
                  className={cn(
                    "border-l border-border/40 px-3 py-2.5",
                    row.mono && "font-mono text-[0.78125rem]",
                    !row.before.trim() && "text-muted-foreground/60",
                  )}
                >
                  {row.before.trim() || <span className="italic">empty</span>}
                </div>

                <div
                  className={cn(
                    "border-l border-border/40 px-3 py-2.5",
                    row.mono && "font-mono text-[0.78125rem]",
                    added && "bg-success/[0.05]",
                    changed && "bg-primary/[0.04]",
                  )}
                >
                  <div className="flex items-start gap-1.5">
                    {row.after.trim() ? (
                      <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden />
                    ) : (
                      <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/50" aria-hidden />
                    )}
                    <div className="min-w-0">
                      <p className={cn("break-words leading-snug", !row.after.trim() && "text-muted-foreground/60")}>
                        {row.after.trim() || "—"}
                      </p>
                      {row.note ? (
                        <p className="mt-0.5 flex items-center gap-1 text-[0.6875rem] text-success">
                          <CheckCircle2 className="size-3" aria-hidden />
                          {row.noteUrl ? (
                            <a
                              href={row.noteUrl}
                              target="_blank"
                              rel="noreferrer noopener"
                              className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
                            >
                              {row.note}
                              <ExternalLink className="size-3" aria-hidden />
                            </a>
                          ) : (
                            row.note
                          )}
                        </p>
                      ) : null}
                    </div>
                  </div>
                </div>
              </motion.div>
            );
          })}
        </div>

        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Green cells were empty in the input. The source row links to the
          first-party document the values were read from — or, when no document
          was retrieved, to the brand's approved manufacturer site. Either way,
          every field is traceable back to the manufacturer's own web property.
        </p>
      </CardContent>
    </Card>
  );
}

