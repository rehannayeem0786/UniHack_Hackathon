import { useEffect, useMemo, useState } from "react";
import { BookOpen, Fingerprint, Globe, Type } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Field, Skeleton } from "@/components/ui/misc";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { humanKey, percent } from "@/lib/format";

const SURFACE_LABELS: Record<string, string> = {
  long: "Product page (LONG_DESC1)",
  short: "Search result (SHORT_DESC)",
  retail: "Retail label (RETAIL_DESC)",
  mobile: "Mobile app (MOBILE_DESC)",
  invoice: "Till receipt (INVOICE_DESC)",
};

function LookupTable({
  caption,
  keyHeader,
  valueHeader,
  entries,
  limit = 40,
}: {
  caption: string;
  keyHeader: string;
  valueHeader: string;
  entries: Record<string, string>;
  limit?: number;
}) {
  const rows = Object.entries(entries).sort(([a], [b]) => a.localeCompare(b));
  const shown = rows.slice(0, limit);

  return (
    <div className="space-y-2">
      <div className="max-h-72 overflow-auto rounded-md border border-border/70">
        <Table caption={caption}>
          <TableHeader>
            <TableRow>
              <TableHead>{keyHeader}</TableHead>
              <TableHead>{valueHeader}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map(([key, value]) => (
              <TableRow key={key}>
                <TableCell className="font-mono text-xs">{key}</TableCell>
                <TableCell className="text-xs">{value}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <p className="tabular text-xs text-muted-foreground">
        {shown.length} of {rows.length} entries
      </p>
    </div>
  );
}

function GrammarViewer({ classpath }: { classpath: string }) {
  const style = useAsync(() => api.style(classpath), { immediate: false });

  useEffect(() => {
    if (classpath) void style.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classpath]);

  if (style.loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, index) => (
          <Skeleton key={index} className="h-28 w-full" />
        ))}
      </div>
    );
  }

  if (style.error) return <ErrorState message={style.error} />;
  if (!style.data) return null;

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-border/60 bg-surface/50 p-3">
        <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Attribute template ({style.data.template.length} labels, fixed order)
        </p>
        <div className="flex flex-wrap gap-1.5">
          {style.data.template.map((label, index) => (
            <Badge key={label} variant="outline" className="text-[0.6875rem]">
              <span className="tabular mr-1 opacity-50">{index + 1}</span>
              {label}
            </Badge>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {Object.entries(style.data.surfaces).map(([surface, detail]) => (
          <Card key={surface}>
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-2">
                <CardTitle as="h4" className="text-sm">
                  {SURFACE_LABELS[surface] ?? surface}
                </CardTitle>
                <Badge variant={detail.rows_learned_from ? "primary" : "outline"} className="text-[0.625rem]">
                  {detail.rows_learned_from
                    ? `${detail.rows_learned_from} row${detail.rows_learned_from === 1 ? "" : "s"}`
                    : "generalised"}
                </Badge>
              </div>
              <CardDescription className="text-xs">
                {detail.order.length
                  ? `${detail.order.length} attribute${detail.order.length === 1 ? "" : "s"}, written in this order`
                  : "This surface carries no attributes for this category."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {detail.order.length ? (
                <ol className="space-y-1">
                  {detail.order.map((label, index) => {
                    const suffix = detail.suffixes[label];
                    const rendered =
                      suffix && suffix[1]
                        ? `<value>${suffix[0]}${suffix[1]}`
                        : "<value>";
                    return (
                      <li
                        key={label}
                        className="flex items-baseline justify-between gap-3 border-b border-border/40 py-1 text-xs last:border-0"
                      >
                        <span className="flex min-w-0 items-baseline gap-2">
                          <span className="tabular w-4 shrink-0 text-muted-foreground">
                            {index + 1}
                          </span>
                          <span className="truncate">{label}</span>
                        </span>
                        <code className="shrink-0 rounded bg-secondary px-1.5 py-0.5 text-[0.6875rem] text-primary">
                          {rendered}
                        </code>
                      </li>
                    );
                  })}
                </ol>
              ) : (
                <p className="text-xs text-muted-foreground">—</p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

export function KnowledgePanel() {
  const knowledge = useAsync(() => api.knowledge());
  const [classpath, setClasspath] = useState<string>();

  const classpaths = useMemo(() => knowledge.data?.classpaths ?? [], [knowledge.data]);

  useEffect(() => {
    if (!classpath && classpaths.length) setClasspath(classpaths[0]);
  }, [classpaths, classpath]);

  if (knowledge.loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (knowledge.error) {
    return (
      <ErrorState
        message={knowledge.error}
        action={
          <Button size="sm" variant="secondary" onClick={() => void knowledge.run()}>
            Retry
          </Button>
        }
      />
    );
  }

  const data = knowledge.data;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle as="h2" className="flex items-center gap-2">
            <BookOpen className="size-4 text-primary" aria-hidden />
            What the pipeline learned
          </CardTitle>
          <CardDescription className="max-w-3xl">
            The official reference pack — manufacturer master list, cross-category list of
            values, UOM standards — is not present in this workspace. Rather than stub it
            out, the equivalent lookups are reconstructed from the labelled rows in the
            training fold. The description grammar is mined the same way instead of being
            hardcoded per category. This page is that inference, printed, so it can be
            checked rather than taken on trust.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Object.entries(data.summary).map(([key, value]) => (
              <div key={key} className="rounded-md border border-border/60 bg-surface/50 p-3">
                <dt className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                  {humanKey(key)}
                </dt>
                <dd className="tabular mt-1 text-lg font-semibold">{value.toLocaleString()}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle as="h3" className="flex items-center gap-2">
            <Type className="size-4 text-primary" aria-hidden />
            Description grammar by category
          </CardTitle>
          <CardDescription className="max-w-3xl">
            For each surface: which attributes it includes, the order it writes them in,
            and how each value is rendered. <code className="rounded bg-secondary px-1 text-xs">Cover Type</code>{" "}
            becomes <code className="rounded bg-secondary px-1 text-xs">Duplex Receptacle Cover</code> on the
            product page, and <code className="rounded bg-secondary px-1 text-xs">Length</code> becomes{" "}
            <code className="rounded bg-secondary px-1 text-xs">4 in Length</code> there but{" "}
            <code className="rounded bg-secondary px-1 text-xs">4 in L</code> on the retail label. None of
            that was written by hand.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Category" hint={`${classpaths.length} classpaths learned`}>
            <Select value={classpath} onValueChange={setClasspath}>
              <SelectTrigger aria-label="Select a category">
                <SelectValue placeholder="Choose a category" />
              </SelectTrigger>
              <SelectContent className="max-h-80">
                {classpaths.map((path) => (
                  <SelectItem key={path} value={path}>
                    {path.split(">").slice(-2).join(" › ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {classpath ? <GrammarViewer classpath={classpath} /> : null}
        </CardContent>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Fingerprint className="size-4 text-primary" aria-hidden />
              Part-number prefix → brand
            </CardTitle>
            <CardDescription className="text-xs">
              Catalogue numbering is brand specific, and it settles cases a supplier string
              cannot: a buying co-op fronts six unrelated brands, but every DR7xxx is a
              Speed Queen. A prefix is only kept when it points at one brand in at least{" "}
              {percent(0.85, 0)} of the rows that use it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LookupTable
              caption="Learned part-number prefixes and the brand each implies"
              keyHeader="Prefix"
              valueHeader="Brand"
              entries={data.mpn_prefixes}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Type className="size-4 text-primary" aria-hidden />
              Invoice abbreviations
            </CardTitle>
            <CardDescription className="text-xs">
              Mined by aligning the 40-character invoice lines against the words the same
              product is described by elsewhere: <span className="font-mono">RECEPTACLE</span> →{" "}
              <span className="font-mono">RCPT</span>.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LookupTable
              caption="Learned invoice abbreviations"
              keyHeader="Word"
              valueHeader="Abbreviation"
              entries={data.invoice_abbreviations}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Globe className="size-4 text-primary" aria-hidden />
              Approved sourcing domains
            </CardTitle>
            <CardDescription className="text-xs">
              The content guidelines require product data to come from the manufacturer's
              own site and exclude marketplaces and distributor sites. These are the
              domains observed in approved rows, keyed by brand.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LookupTable
              caption="Approved manufacturer domains by brand"
              keyHeader="Brand"
              valueHeader="Domain"
              entries={data.sourcing_domains}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle as="h3" className="flex items-center gap-2 text-sm">
              <Fingerprint className="size-4 text-primary" aria-hidden />
              Brand → parent manufacturer
            </CardTitle>
            <CardDescription className="text-xs">
              Brand and manufacturer are different fields and frequently different
              companies: Speed Queen is made by Alliance Laundry Systems, Profile by
              Haier.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <LookupTable
              caption="Brand to parent manufacturer"
              keyHeader="Brand"
              valueHeader="Manufacturer"
              entries={data.brands}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
