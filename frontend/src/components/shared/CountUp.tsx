import { useEffect, useRef, useState } from "react";
import { animate } from "framer-motion";

import { cn } from "@/lib/utils";

/**
 * Animates a numeric string from 0 to its value on first mount — the hero and
 * the metric cards count up when they appear, which reads as "live system"
 * rather than "static page". Non-numeric strings render unchanged, so any
 * value can be passed through it.
 *
 * Respects prefers-reduced-motion by jumping straight to the final value.
 */

const NUMBER_PREFIX = /^([\d,.]+)(.*)$/;

function parse(value: string): { target: number; suffix: string; decimals: number } | null {
  const match = NUMBER_PREFIX.exec(value.trim());
  if (!match) return null;
  const raw = match[1];
  if (!/\d/.test(raw)) return null;
  const decimals = raw.includes(".") ? raw.split(".")[1].length : 0;
  const target = Number(raw.replace(/,/g, ""));
  if (!Number.isFinite(target)) return null;
  return { target, suffix: match[2], decimals };
}

export function CountUp({
  value,
  duration = 0.9,
  className,
}: {
  value: string;
  duration?: number;
  className?: string;
}) {
  const parsed = parse(value);
  const [display, setDisplay] = useState(() => (parsed ? "0" : value));
  const started = useRef(false);

  useEffect(() => {
    if (!parsed || started.current) return;
    started.current = true;

    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setDisplay(value);
      return;
    }

    const controls = animate(0, parsed.target, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate(latest) {
        setDisplay(
          latest.toLocaleString(undefined, {
            minimumFractionDigits: parsed.decimals,
            maximumFractionDigits: parsed.decimals,
          }),
        );
      },
      onComplete() {
        setDisplay(value); // land on the exact authored string
      },
    });
    return () => controls.stop();
  }, [parsed, value, duration]);

  return (
    <span className={cn("tabular", className)}>
      {parsed ? `${display}${parsed.suffix}` : value}
    </span>
  );
}
