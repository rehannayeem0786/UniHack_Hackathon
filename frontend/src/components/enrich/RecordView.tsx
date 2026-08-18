import { motion } from "framer-motion";
import {
  AlertTriangle,
  BookOpenCheck,
  ExternalLink,
  FileSpreadsheet,
  Link2,
  Monitor,
  Receipt,
  Search,
  ShoppingBag,
  Tag,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/misc";
import { InfoTip } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ConfidenceBadge, ConfidenceMeter } from "@/components/shared/Confidence";
import type { RecordPayload } from "@/lib/api";
import { humanKey, wordDiff } from "@/lib/format";
import { cn } from "@/lib/utils";

/* --- the five surfaces -------------------------------------------------- */

const SURFACES = [
  {
    key: "invoice_desc" as const,
    truthKey: "INVOICE_DESC",
    title: "Invoice line",
    where: "Till receipt",
    icon: Receipt,
    rule: "Max 40 characters, ALL CAPS, noun first, house abbreviations.",
    limit: 40,
  },
  {
    key: "mobile_desc" as const,
    truthKey: "MOBILE_DESC",
    title: "Mobile",
    where: "Mobile app",
    icon: Monitor,
    rule: "60–80 characters. Manufacturer and brand deduplicated, no symbols.",
    range: [60, 80] as const,
  },
  {
    key: "short_desc" as const,
    truthKey: "SHORT_DESC",
    title: "Search result",
    where: "Search row",
    icon: Search,
    rule: "Brand + Series + MPN + Item type, then the category's key attributes.",
  },
  {
    key: "retail_desc" as const,
    truthKey: "RETAIL_DESC",
    title: "Retail label",
    where: "Shelf label",
    icon: Tag,
    rule: "Series + Item type and key attributes. No brand, no part number.",
  },
  {
    key: "long_desc" as const,
    truthKey: "LONG_DESC1",
    title: "Product page",
    where: "Product page",
    icon: ShoppingBag,
    rule: "Every attribute in the learned order, then Additional Information.",
  },
];

function LengthBadge({
  text,
  limit,
  range,
}: {
  text: string;
  limit?: number;
  range?: readonly [number, number];
}) {
  const length = text.length;
  let ok = true;
  let rule = `${length} characters`;

  if (limit !== undefined) {
    ok = length <= limit;
    rule = `${length} / ${limit} characters`;
  } else if (range) {
    ok = length >= range[0] && length <= range[1];
    rule = `${length} chars, target ${range[0]}–${range[1]}`;
  }

  if (limit === undefined && !range) {
    return <span className="tabular text-xs text-muted-foreground">{rule}</span>;
  }
  return (
    <Badge variant={ok ? "success" : "destructive"} className="tabular text-[0.625rem]">
      {rule}
    </Badge>
  );
}

function SurfaceCard({
  surface,
  value,
  truth,
  index,
}: {
  surface: (typeof SURFACES)[number];
  value: string | null;
  truth?: string;
  index: number;
}) {
  const text = value ?? "";
  const diff = truth ? wordDiff(text, truth) : null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
    >
      <Card className="h-full">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2">
              <surface.icon className="size-4 shrink-0 text-primary" aria-hidden />
              <CardTitle as="h4" className="text-sm">
                {surface.title}
              </CardTitle>
              <Badge variant="outline" className="text-[0.625rem]">
                {surface.where}
              </Badge>
            </div>
            <LengthBadge text={text} limit={surface.limit} range={surface.range} />
          </div>
          <CardDescription className="text-xs">{surface.rule}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p
            className={cn(
              "rounded-md border border-border/60 bg-surface/60 p-3 text-sm leading-relaxed",
              surface.key === "invoice_desc" && "font-mono text-[0.8125rem] tracking-tight",
            )}
          >
            {diff ? (
              <>
                {diff.map((token, i) => (
                  <span
                    key={`${token.word}-${i}`}
                    className={cn(
                      !token.matched &&
                        "rounded bg-warning/15 px-0.5 text-warning underline decoration-warning/40 decoration-dotted underline-offset-2",
                    )}
                  >
                    {token.word}
                    {i < diff.length - 1 ? " " : ""}
                  </span>
                ))}
              </>
            ) : (
              text || <span className="text-muted-foreground">— not generated —</span>
            )}
          </p>

          {truth ? (
            <details className="group">
              <summary className="cursor-pointer list-none text-xs font-medium text-muted-foreground transition-colors hover:text-foreground">
                <span className="underline decoration-dotted underline-offset-2">
                  Compare with the human-written reference
                </span>
              </summary>
              <p className="mt-2 rounded-md border border-success/25 bg-success/[0.06] p-3 text-sm leading-relaxed">
                {truth}
              </p>
              <p className="mt-1.5 text-xs text-muted-foreground">
                Highlighted words above do not appear in the reference. Most gaps are
                specifications that exist only on the manufacturer's site, not in the
                one-line input.
              </p>
            </details>
          ) : null}
        </CardContent>
      </Card>
    </motion.div>
  );
}

/* --- identity ----------------------------------------------------------- */

function IdentityRow({
  label,
  value,
  truth,
  confidence,
  mono,
}: {
  label: string;
  value: string | null | undefined;
  truth?: string;
  confidence?: number;
  mono?: boolean;
}) {
  const shown = value ?? "";
  const matches = truth !== undefined && truth !== "" ? shown === truth : undefined;

  return (
    <div className="flex flex-col gap-1 border-b border-border/50 py-2.5 last:border-0 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="w-44 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn("text-sm", mono && "font-mono text-[0.8125rem]", !shown && "text-muted-foreground")}>
            {shown || "—"}
          </span>
          {matches === true ? (
            <Badge variant="success" className="text-[0.625rem]">
              matches reference
            </Badge>
          ) : matches === false ? (
            <InfoTip label={`Reference value: ${truth}`}>
              <Badge variant="warning" className="cursor-help text-[0.625rem]">
                differs
              </Badge>
            </InfoTip>
          ) : null}
          {confidence !== undefined ? <ConfidenceBadge value={confidence} /> : null}
        </div>
        {matches === false ? (
          <p className="text-xs text-muted-foreground">
            Reference: <span className="text-foreground/80">{truth}</span>
          </p>
        ) : null}
      </dd>
    </div>
  );
}

/* --- retrieved sources -------------------------------------------------- */

const KIND_LABEL: Record<string, string> = {
  specification: "Specification sheet",
  manual: "Manual",
  "product-page": "Product page",
  "support-page": "Support page",
  other: "Document",
};

/**
 * What the pipeline read, and which values it confirmed there.
 *
 * The point of this panel is that a reviewer never has to take a value on
 * trust. Anything listed under "confirmed verbatim" was found word for word in
 * the document it is attributed to, so checking it is a click rather than an
 * investigation. When nothing was retrieved the panel says so plainly, because a
 * missing source is itself a useful thing for a reviewer to know.
 */
function SourcesPanel({ record }: { record: RecordPayload }) {
  const citations = record.citations ?? [];
  const grounded = Object.entries(record.grounded ?? {});

  // Group the confirmed values under the document that confirms each of them.
  const byUrl = new Map<string, string[]>();
  for (const [label, url] of grounded) {
    byUrl.set(url, [...(byUrl.get(url) ?? []), label]);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle as="h3" className="flex items-center gap-2 text-sm">
          <BookOpenCheck className="size-4 text-primary" aria-hidden />
          Retrieved sources
          {grounded.length > 0 ? (
            <Badge variant="success" className="ml-1">
              {grounded.length} value{grounded.length === 1 ? "" : "s"} confirmed
            </Badge>
          ) : null}
        </CardTitle>
        <CardDescription className="text-xs">
          Read from the manufacturer&apos;s own site and documents only. Marketplaces and
          distributors are refused by policy, and a page is discarded unless the part
          number appears on it.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {citations.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No first-party source was retrieved for this part
            {record.research_note ? (
              <>
                {" — "}
                <span className="italic">{record.research_note}</span>
              </>
            ) : null}
            . Values below come from the input row and the patterns mined from the
            labelled set, and are marked accordingly.
          </p>
        ) : (
          <ul className="space-y-3">
            {citations.map((citation) => {
              const confirmed = byUrl.get(citation.url) ?? [];
              return (
                <li
                  key={citation.url}
                  className="rounded-md border border-border/60 bg-secondary/30 p-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{KIND_LABEL[citation.kind] ?? citation.kind}</Badge>
                    {citation.source === "third-party" ? (
                      <Badge variant="default" className="text-[0.6875rem]">
                        third-party
                      </Badge>
                    ) : null}
                    {citation.from_cache ? (
                      <Badge variant="default" className="text-[0.6875rem]">
                        cached
                      </Badge>
                    ) : null}
                    <span className="text-xs text-muted-foreground">
                      {citation.characters.toLocaleString()} characters read
                      {citation.table_rows > 0
                        ? `, ${citation.table_rows} specification rows`
                        : ""}
                    </span>
                  </div>
                  <a
                    href={citation.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-1 flex items-start gap-1.5 break-all text-xs font-medium text-primary underline-offset-2 hover:underline"
                  >
                    <ExternalLink className="mt-0.5 size-3 shrink-0" aria-hidden />
                    {citation.url}
                  </a>
                  {confirmed.length > 0 ? (
                    <p className="mt-2 text-xs text-muted-foreground">
                      <span className="font-medium text-foreground">
                        Confirmed verbatim here:
                      </span>{" "}
                      {confirmed.map((label) => humanKey(label)).join(", ")}
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/* --- the record --------------------------------------------------------- */

export function RecordView({ record }: { record: RecordPayload }) {
  const { output, input, truth } = record;
  const filled = record.attributes.filter((a) => a.value);

  return (
    <div className="space-y-6">
      {/* Input vs identity */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
        <Card>
          <CardHeader>
            <CardTitle as="h3">Raw input</CardTitle>
            <CardDescription>
              What the distributor supplied. One abbreviated string, placeholder brand
              fields, and a supplier name that is often a buying co-op rather than a
              manufacturer.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="rounded-md border border-border/60 bg-surface/60 p-3 font-mono text-[0.8125rem] leading-relaxed">
              {input.description || "—"}
            </p>
            <dl className="space-y-0 text-sm">
              <IdentityRow label="Part number" value={record.part_number} mono />
              <IdentityRow label="Mfg part number" value={input.mpn} mono />
              <IdentityRow label="Supplier string" value={input.supplier} />
              <IdentityRow
                label="Category triple"
                value={[input.dept, input.class, input.fine].filter(Boolean).join(" › ")}
              />
              <IdentityRow
                label="Brand hints"
                value={input.brand_hints.length ? input.brand_hints.join(", ") : "none — all placeholders"}
              />
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3">Resolved identity</CardTitle>
            <CardDescription>
              Brand and manufacturer are snapped onto approved spellings, symbols
              included, so they match the master list exactly.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="space-y-0">
              <IdentityRow
                label="Manufacturer"
                value={output.manufacturer_name}
                truth={truth?.MANUFACTURER_NAME}
                confidence={record.confidence.manufacturer_name}
              />
              <IdentityRow
                label="Brand"
                value={output.brand_name}
                truth={truth?.BRAND_NAME}
                confidence={record.confidence.brand_name}
              />
              <IdentityRow label="Part number" value={output.mpn} truth={truth?.MANUFACTURER_PART_NUMBER} mono />
              <IdentityRow
                label="Classpath"
                value={output.classpath}
                truth={truth?.Classpath}
                confidence={record.confidence.classpath}
              />
              <IdentityRow
                label="Product name"
                value={output.product_name}
                truth={truth?.["Product Name"]}
                confidence={record.confidence.product_name}
              />
              <IdentityRow label="Series" value={output.series} />
              {output.with_clause ? (
                <IdentityRow label="With" value={output.with_clause} />
              ) : null}
              {output.mfr_url ? (
                <div className="flex items-baseline gap-4 py-2.5">
                  <dt className="w-44 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                    Source
                  </dt>
                  <dd className="min-w-0 flex-1">
                    <a
                      href={output.mfr_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="inline-flex items-center gap-1.5 text-sm text-primary underline-offset-4 hover:underline"
                    >
                      {output.mfr_url}
                      <ExternalLink className="size-3" aria-hidden />
                    </a>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Approved manufacturer domain. Marketplaces and distributor sites are
                      excluded by the sourcing rules.
                    </p>
                  </dd>
                </div>
              ) : null}
            </dl>
          </CardContent>
        </Card>
      </div>

      {/* Surfaces */}
      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold tracking-tight">Five description surfaces</h3>
          <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
            The same product written five times for five places it appears. Word order
            and which attributes are included differ per surface and per category, and
            both were mined from labelled rows rather than hardcoded.
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          {SURFACES.map((surface, index) => (
            <div key={surface.key} className={surface.key === "long_desc" ? "lg:col-span-2" : undefined}>
              <SurfaceCard
                surface={surface}
                value={output[surface.key]}
                truth={truth?.[surface.truthKey]}
                index={index}
              />
            </div>
          ))}
        </div>
        {output.marketing_desc ? (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle as="h4" className="text-sm">
                Marketing copy
              </CardTitle>
              <CardDescription className="text-xs">
                The one free-prose field. The model is given only attributes that already
                passed validation and told to use nothing else.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <p className="text-sm leading-relaxed">{output.marketing_desc}</p>
            </CardContent>
          </Card>
        ) : null}
      </section>

      {/* Attributes */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold tracking-tight">Attributes</h3>
            <p className="max-w-3xl text-sm leading-relaxed text-muted-foreground">
              Emitted in the category template's order. Unfilled labels are kept rather
              than dropped, so a gap is visible instead of silently absent.
            </p>
          </div>
          <Badge variant="outline" className="tabular">
            {filled.length} of {record.attributes.length} filled
          </Badge>
        </div>
        <Table caption="Extracted attributes with value, unit, source and confidence">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[26%]">Label</TableHead>
              <TableHead className="w-[34%]">Value</TableHead>
              <TableHead className="w-[8%]">UOM</TableHead>
              <TableHead className="w-[18%]">Source</TableHead>
              <TableHead className="w-[14%]">Confidence</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {record.attributes.map((attr) => {
              const empty = !attr.value;
              return (
                <TableRow key={attr.label} data-muted={empty}>
                  <TableCell className="font-medium">{attr.label}</TableCell>
                  <TableCell>
                    {attr.value ?? <span className="text-muted-foreground">not found</span>}
                  </TableCell>
                  <TableCell className="tabular text-muted-foreground">{attr.uom ?? ""}</TableCell>
                  <TableCell>
                    <code className="rounded bg-secondary px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground">
                      {attr.source}
                    </code>
                  </TableCell>
                  <TableCell>{empty ? null : <ConfidenceMeter value={attr.confidence} />}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </section>

      {/* Retrieved first-party sources */}
      <SourcesPanel record={record} />

      {/* Provenance, assets, review */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Link2 className="size-4 text-primary" aria-hidden />
              Provenance
            </CardTitle>
            <CardDescription className="text-xs">
              Every field records how it was decided, so an output can be audited rather
              than trusted.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="space-y-0 text-sm">
              {Object.entries(record.provenance).map(([key, value]) => (
                <div
                  key={key}
                  className="flex flex-col gap-0.5 border-b border-border/50 py-2 last:border-0 sm:flex-row sm:gap-4"
                >
                  <dt className="w-40 shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
                    {humanKey(key)}
                  </dt>
                  <dd className="min-w-0 flex-1 break-words font-mono text-[0.75rem] leading-relaxed">
                    {value}
                  </dd>
                </div>
              ))}
              {Object.keys(record.provenance).length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">No provenance recorded.</p>
              ) : null}
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <FileSpreadsheet className="size-4 text-primary" aria-hidden />
              Assets, documents &amp; packaging
            </CardTitle>
            <CardDescription className="text-xs">
              Filenames follow the naming convention observed across the labelled set. A
              document is only named for a brand known to publish that document type.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {Object.keys(record.extras).length ? (
              <dl className="space-y-0 text-sm">
                {Object.entries(record.extras).map(([key, value]) => (
                  <div
                    key={key}
                    className="flex flex-col gap-0.5 border-b border-border/50 py-2 last:border-0 sm:flex-row sm:gap-4"
                  >
                    <dt className="w-40 shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
                      {key}
                    </dt>
                    <dd className="min-w-0 flex-1 break-words font-mono text-[0.75rem]">{value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-sm text-muted-foreground">
                No assets could be named for this brand from the training fold.
              </p>
            )}

            {record.approvals.length ? (
              <>
                <Separator className="my-4" />
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Standards &amp; approvals
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {record.approvals.map((approval) => (
                    <Badge key={approval} variant="default" className="text-[0.6875rem]">
                      {approval}
                    </Badge>
                  ))}
                </div>
              </>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {record.issues.length ? (
        <Card className="border-warning/30 bg-warning/[0.04]">
          <CardHeader className="pb-3">
            <CardTitle as="h3" className="flex items-center gap-2 text-sm text-warning">
              <AlertTriangle className="size-4" aria-hidden />
              Flagged for human review
            </CardTitle>
            <CardDescription className="text-xs">
              Reporting a gap is more useful than filling it with a guess. These are the
              things this row could not establish.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm">
              {record.issues.map((issue, index) => (
                <li key={`${issue}-${index}`} className="flex gap-2">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-warning" aria-hidden />
                  <span className="text-foreground/85">{issue}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
