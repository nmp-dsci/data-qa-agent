// KitEmpty — the one empty state. Before this, every list that could be empty
// invented its own grey sentence, which is how "nothing here" ends up reading
// like a failure instead of a starting point.
//
// The rule it encodes: an empty panel names what would live there, says how to
// put something in it, and — where the caller can — offers the control that
// does it. Never an apology, never a shrug.
import { ReactNode } from "react";
import { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export function KitEmpty({
  icon: Icon,
  title,
  hint,
  action,
  className,
}: {
  /** A lucide glyph for the thing that is missing (goldens → Star, …). */
  icon: LucideIcon;
  title: string;
  /** One sentence: what to do about it. */
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("kit-empty", className)}>
      <span className="kit-empty-mark" aria-hidden="true">
        <Icon size={18} strokeWidth={1.6} />
      </span>
      <b>{title}</b>
      {hint && <span>{hint}</span>}
      {action}
    </div>
  );
}
