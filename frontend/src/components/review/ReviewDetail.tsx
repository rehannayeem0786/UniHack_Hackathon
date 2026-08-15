import { useEffect, useMemo, useState } from "react";
import { Check, ExternalLink, Save, Undo2 } from "lucide-react";
import { toast } from "sonner";

import { ConfidenceBadge } from "@/components/shared/Confidence";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState, Field, Label, Separator, Skeleton } from "@/components/ui/misc";
import { useAsync } from "@/hooks/useAsync";
import { api, type ReviewRecordPayload } from "@/lib/api";
import { fieldLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Core output fields a reviewer can accept or override, in display order. */
const FIELD_ORDER = [
  "manufacturer_name",
  "brand_name",
  "mpn",
  "classpath",
  "product_name",
  "series",
  "with_clause",
  "invoice_desc",
  "mobile_desc",
  "short_desc",
  "long_desc",
  "retail_desc",
  "marketing_desc",
  "mfr_url",
] as const;

type FieldKey = (typeof FIELD_ORDER)[number];

/** Map an output field to the ground-truth column, when one exists. */
const TRUTH_KEY: Partial<Record<FieldKey, string>> = {
  manufacturer_name: "MANUFACTURER_NAME",
  brand_name: "BRAND_NAME",
  mpn: "MANUFACTURER_PART_NUMBER",
  classpath: "Classpath",
  product_name: "Product Name",
  invoice_desc: "INVOICE_DESC",
  mobile_desc: "MOBILE_DESC",
  short_desc: "SHORT_DESC",
  long_desc: "LONG_DESC1",
  retail_desc: "RETAIL_DESC",
  marketing_desc: "MARKETING_DESCRIPTION",
};

interface AttributeEdit {
  value: string;
  uom: string;
}

export function ReviewDetail({
  partNumber,
  onDecided,
}: {
  partNumber: string;
  onDecided: () => void;
}) {
  const record = useAsync(() => api.reviewRecord(partNumber, "holdout"));
  const data = record.data;

  // Editable copies of the suggested values. The parent keys this component by
  // part number, so opening a different record remounts it with a clean slate.
  const [fields, setFields] = useState<Record<string, string>>({});
  const [attrs, setAttrs] = useState<Record<string, AttributeEdit>>({});
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!data) return;
    const next: Record<string, string> = {};
    for (const key of FIELD_ORDER) next[key] = data.output[key] ?? "";
    setFields(next);

    const attrEdits: Record<string, AttributeEdit> = {};
    for (const attr of data.attributes) {
      attrEdits[attr.label] = { value: attr.value ?? "", uom: attr.uom ?? "" };
    }
    setAttrs(attrEdits);
    setNotes("");
  }, [data]);

  // Which fields differ from what the pipeline suggested.
  const changedFields = useMemo(() => {
    if (!data) return new Set<string>();
    const changed = new Set<string>();
    for (const key of FIELD_ORDER) {
      if ((fields[key] ?? "") !== (data.output[key] ?? "")) changed.add(key);
    }
    return changed;
  }, [data, fields]);

  const changedAttrs = useMemo(() => {
    if (!data) return new Set<string>();
    const changed = new Set<string>();
    for (const attr of data.attributes) {
      const edit = attrs[attr.label];
      if (!edit) continue;
      if (edit.value !== (attr.value ?? "") || edit.uom !== (attr.uom ?? "")) {
        changed.add(attr.label);
      }
    }
    return changed;
  }, [data, attrs]);

  const hasChanges = changedFields.size > 0 || changedAttrs.size > 0;

  async function submit(status: "approved" | "corrected") {
    if (!data) return;
    setSubmitting(true);
    try {
      const payload: Parameters<typeof api.reviewDecision>[1] = { status, notes };
      if (status === "corrected") {
        payload.fields = {};
        for (const key of changedFields) payload.fields[key] = fields[key] || null;
        payload.attributes = data.attributes
          .filter((a) => changedAttrs.has(a.label))
          .map((a) => ({
            label: a.label,
            value: attrs[a.label]?.value || null,
            uom: attrs[a.label]?.uom || null,
          }));
      }
      await api.reviewDecision(partNumber, payload);
      toast.success(status === "approved" ? "Record approved" : "Corrections saved", {
        description:
          status === "approved"
            ? "The pipeline's values were accepted as generated."
            : "Your overrides are stored and will be applied on every future run.",
      });
      onDecided();
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Could not save the decision.";
      toast.error("Review failed", { description: message });
    } finally {
      setSubmitting(false);
    }
  }

  if (record.loading && !data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (record.error || !data) {
    return <ErrorState message={record.error ?? "Record not found."} />;
  }

  return (
    <ReviewDetailBody
      data={data}
      fields={fields}
      attrs={attrs}
      notes={notes}
      submitting={submitting}
      hasChanges={hasChanges}
      changedFields={changedFields}
      changedAttrs={changedAttrs}
      onField={(key, value) => setFields((prev) => ({ ...prev, [key]: value }))}
      onAttr={(label, edit) => setAttrs((prev) => ({ ...prev, [label]: edit }))}
      onNotes={setNotes}
      onDiscard={() => {
        const next: Record<string, string> = {};
        for (const key of FIELD_ORDER) next[key] = data.output[key] ?? "";
        setFields(next);
        const reset: Record<string, AttributeEdit> = {};
        for (const attr of data.attributes) {
          reset[attr.label] = { value: attr.value ?? "", uom: attr.uom ?? "" };
        }
        setAttrs(reset);
      }}
      onSubmit={submit}
    />
  );
}

interface DetailBodyProps {
  data: ReviewRecordPayload;
  fields: Record<string, string>;
  attrs: Record<string, AttributeEdit>;
  notes: string;
  submitting: boolean;
  hasChanges: boolean;
  changedFields: Set<string>;
  changedAttrs: Set<string>;
  onField: (key: FieldKey, value: string) => void;
  onAttr: (label: string, edit: AttributeEdit) => void;
  onNotes: (value: string) => void;
  onDiscard: () => void;
  onSubmit: (status: "approved" | "corrected") => void;
}

function ReviewDetailBody({
  data,
  fields,
  attrs,
  notes,
  submitting,
  hasChanges,
  changedFields,
  changedAttrs,
  onField,
  onAttr,
  onNotes,
  onDiscard,
  onSubmit,
}: DetailBodyProps) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div className="min-w-0">
          <CardTitle as="h2" className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{data.part_number}</span>
            <StatusPill status={data.review_status} />
            {data.needs_review ? <Badge variant="warning">flagged</Badge> : null}
          </CardTitle>
          <CardDescription className="mt-1 break-words">
            {data.input.description || "No input description"}
          </CardDescription>
        </div>
        <div className="shrink-0 text-right">
          <ConfidenceBadge value={data.confidence.overall} label="overall" />
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <FieldEditor data={data} fields={fields} changed={changedFields} onChange={onField} />

        <Separator />

        <AttributeEditor data={data} attrs={attrs} changed={changedAttrs} onChange={onAttr} />

        {data.citations.length ? (
          <>
            <Separator />
            <EvidenceList data={data} />
          </>
        ) : null}

        <Separator />

        <div className="space-y-3">
          <Field label="Reviewer notes" hint="Optional context for the decision.">
            <textarea
              value={notes}
              onChange={(e) => onNotes(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Why was this value changed?"
            />
          </Field>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {hasChanges
                ? `${changedFields.size} field(s) and ${changedAttrs.size} attribute(s) edited.`
                : "No edits — approving accepts the pipeline's values as generated."}
            </p>
            <div className="flex items-center gap-2">
              {hasChanges ? (
                <Button variant="ghost" size="sm" onClick={onDiscard}>
                  <Undo2 /> Discard edits
                </Button>
              ) : null}
              <Button
                variant="secondary"
                onClick={() => onSubmit("approved")}
                loading={submitting}
                disabled={hasChanges}
              >
                <Check /> Approve as generated
              </Button>
              <Button
                onClick={() => onSubmit("corrected")}
                loading={submitting}
                disabled={!hasChanges}
              >
                <Save /> Save corrections
              </Button>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/** Side-by-side editor: suggested value, reference value, and an override box. */
function FieldEditor({
  data,
  fields,
  changed,
  onChange,
}: {
  data: ReviewRecordPayload;
  fields: Record<string, string>;
  changed: Set<string>;
  onChange: (key: FieldKey, value: string) => void;
}) {
  return (
    <div className="space-y-3">
      <Label>Core fields</Label>
      <div className="grid gap-3">
        {FIELD_ORDER.map((key) => {
          const suggested = data.output[key] ?? "";
          const truthKey = TRUTH_KEY[key];
          const reference = truthKey ? data.truth?.[truthKey] ?? "" : "";
          const isLong = key.endsWith("_desc") || key === "mfr_url";
          const edited = changed.has(key);

          return (
            <div
              key={key}
              className={cn(
                "grid gap-2 rounded-md border border-border/60 p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.2fr)]",
                edited && "border-primary/50 bg-primary/[0.04]",
              )}
            >
              <div className="min-w-0">
                <p className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                  {fieldLabel(key)}
                </p>
                <p className="mt-1 break-words text-sm" title={suggested}>
                  {suggested || <span className="text-muted-foreground">—</span>}
                </p>
                <div className="mt-1 flex items-center gap-2">
                  <ConfidenceBadge value={data.confidence[key]} />
                  {data.provenance[key] ? (
                    <span
                      className="truncate text-[0.6875rem] text-muted-foreground"
                      title={data.provenance[key]}
                    >
                      {data.provenance[key]}
                    </span>
                  ) : null}
                </div>
              </div>

              <div className="min-w-0">
                <p className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                  Reference{truthKey ? "" : " (n/a)"}
                </p>
                <p className="mt-1 break-words text-sm text-muted-foreground" title={reference}>
                  {reference || <span>—</span>}
                </p>
              </div>

              <div className="min-w-0">
                <p className="text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
                  Final value {edited ? "(edited)" : ""}
                </p>
                {isLong ? (
                  <textarea
                    value={fields[key] ?? ""}
                    onChange={(e) => onChange(key, e.target.value)}
                    rows={2}
                    className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                ) : (
                  <input
                    value={fields[key] ?? ""}
                    onChange={(e) => onChange(key, e.target.value)}
                    className="mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AttributeEditor({
  data,
  attrs,
  changed,
  onChange,
}: {
  data: ReviewRecordPayload;
  attrs: Record<string, AttributeEdit>;
  changed: Set<string>;
  onChange: (label: string, edit: AttributeEdit) => void;
}) {
  if (!data.attributes.length) return null;
  return (
    <div className="space-y-3">
      <Label>Attributes ({data.attributes.filter((a) => a.value).length} filled)</Label>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {data.attributes.map((attr) => {
          const edit = attrs[attr.label] ?? { value: attr.value ?? "", uom: attr.uom ?? "" };
          const edited = changed.has(attr.label);
          const groundedUrl = data.grounded[attr.label];
          return (
            <div
              key={attr.label}
              className={cn(
                "rounded-md border border-border/60 p-2.5",
                edited && "border-primary/50 bg-primary/[0.04]",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-xs font-medium" title={attr.label}>
                  {attr.label}
                </p>
                {groundedUrl ? (
                  <Badge variant="success" title={`Verified against ${groundedUrl}`}>
                    grounded
                  </Badge>
                ) : null}
              </div>
              <div className="mt-1.5 flex gap-1.5">
                <input
                  value={edit.value}
                  onChange={(e) => onChange(attr.label, { ...edit, value: e.target.value })}
                  placeholder="value"
                  aria-label={`${attr.label} value`}
                  className="w-full min-w-0 rounded-md border border-border bg-background px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
                <input
                  value={edit.uom}
                  onChange={(e) => onChange(attr.label, { ...edit, uom: e.target.value })}
                  placeholder="uom"
                  aria-label={`${attr.label} unit`}
                  className="w-14 shrink-0 rounded-md border border-border bg-background px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EvidenceList({ data }: { data: ReviewRecordPayload }) {
  return (
    <div className="space-y-3">
      <Label>First-party sources read ({data.citations.length})</Label>
      <ul className="space-y-2">
        {data.citations.map((citation) => (
          <li key={citation.url} className="flex items-start gap-2 text-sm">
            <Badge variant="outline" className="mt-0.5 shrink-0">
              {citation.kind}
            </Badge>
            <div className="min-w-0">
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex max-w-full items-center gap-1 break-all text-primary hover:underline"
              >
                {citation.title || citation.url}
                <ExternalLink className="size-3 shrink-0" aria-hidden />
              </a>
              <p className="text-xs text-muted-foreground">
                {citation.characters.toLocaleString("en-US")} characters
                {citation.table_rows ? `, ${citation.table_rows} spec rows` : ""}
                {citation.from_cache ? ", from cache" : ""}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusPill({ status }: { status: ReviewRecordPayload["review_status"] }) {
  if (status === "approved") return <Badge variant="success">approved</Badge>;
  if (status === "corrected") return <Badge variant="primary">corrected</Badge>;
  return <Badge variant="warning">pending</Badge>;
}
