import { useCallback, useEffect, useMemo, useState } from "react";
import { ClipboardCheck, RefreshCw } from "lucide-react";

import { ReviewDetail } from "@/components/review/ReviewDetail";
import { ConfidenceMeter } from "@/components/shared/Confidence";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/misc";
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
import { api, type ReviewQueueRow } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_FILTERS = [
  { value: "pending", label: "Pending review" },
  { value: "all", label: "All records" },
  { value: "approved", label: "Approved" },
  { value: "corrected", label: "Corrected" },
] as const;

export function ReviewPanel() {
  const [status, setStatus] = useState<string>("pending");
  const [selected, setSelected] = useState<string>();
  // Bumped after each submitted decision so the queue re-fetches and the row's
  // status flips without a full page reload.
  const [version, setVersion] = useState(0);

  const queue = useAsync(() => api.reviewQueue("holdout", status, 100), {
    immediate: false,
  });

  useEffect(() => {
    void queue.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, version]);

  const rows = queue.data?.rows ?? [];

  // Keep a valid selection: default to the first row, clear it if it leaves.
  useEffect(() => {
    if (!rows.length) {
      setSelected(undefined);
      return;
    }
    if (!selected || !rows.some((r) => r.part_number === selected)) {
      setSelected(rows[0].part_number);
    }
  }, [rows, selected]);

  const current = useMemo(
    () => rows.find((r) => r.part_number === selected),
    [rows, selected],
  );

  const onDecided = useCallback(() => setVersion((v) => v + 1), []);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
          <div>
            <CardTitle as="h2">Human-in-the-loop review queue</CardTitle>
            <CardDescription>
              Flagged and low-confidence records, most urgent first. Accept a value as
              generated, or override it — every decision is stored and replayed as the
              final pipeline stage, so the correction feeds every future run.
            </CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-44" aria-label="Filter by review status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {STATUS_FILTERS.map((f) => (
                  <SelectItem key={f.value} value={f.value}>
                    {f.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              variant="outline"
              size="icon"
              onClick={() => void queue.run()}
              aria-label="Refresh queue"
            >
              <RefreshCw className={cn(queue.loading && "animate-spin")} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {queue.loading && !queue.data ? (
            <Skeleton className="h-48" />
          ) : queue.error ? (
            <ErrorState message={queue.error} />
          ) : !rows.length ? (
            <EmptyState
              icon={ClipboardCheck}
              title={status === "pending" ? "Nothing waiting on a reviewer" : "No records match"}
              description={
                status === "pending"
                  ? "Every record in this fold has been reviewed. Switch the filter to see approved and corrected rows."
                  : "Try a different status filter."
              }
            />
          ) : (
            <QueueTable rows={rows} selected={selected} onSelect={setSelected} />
          )}
        </CardContent>
      </Card>

      {current ? (
        <ReviewDetail
          key={current.part_number}
          partNumber={current.part_number}
          onDecided={onDecided}
        />
      ) : null}
    </div>
  );
}

function QueueTable({
  rows,
  selected,
  onSelect,
}: {
  rows: ReviewQueueRow[];
  selected: string | undefined;
  onSelect: (partNumber: string) => void;
}) {
  return (
    <Table caption="Records awaiting review, most urgent first">
      <TableHeader>
        <TableRow>
          <TableHead>Part number</TableHead>
          <TableHead>Brand</TableHead>
          <TableHead>Product</TableHead>
          <TableHead>Confidence</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Flags</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow
            key={row.part_number}
            className={cn(
              "cursor-pointer",
              row.part_number === selected && "bg-accent/60",
            )}
            onClick={() => onSelect(row.part_number)}
          >
            <TableCell className="font-mono text-xs">{row.part_number}</TableCell>
            <TableCell>{row.brand || "—"}</TableCell>
            <TableCell className="max-w-[16rem] truncate" title={row.product_name}>
              {row.product_name || "—"}
            </TableCell>
            <TableCell>
              <ConfidenceMeter value={row.confidence} />
            </TableCell>
            <TableCell>
              <StatusBadge status={row.status} />
            </TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {row.needs_review ? <Badge variant="warning">flagged</Badge> : null}
                {row.sourced ? <Badge variant="success">sourced</Badge> : null}
                {row.issues.length ? (
                  <Badge variant="outline" title={row.issues.join("; ")}>
                    {row.issues.length} issue{row.issues.length > 1 ? "s" : ""}
                  </Badge>
                ) : null}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function StatusBadge({ status }: { status: ReviewQueueRow["status"] }) {
  if (status === "approved") return <Badge variant="success">approved</Badge>;
  if (status === "corrected") return <Badge variant="primary">corrected</Badge>;
  return <Badge variant="warning">pending</Badge>;
}
