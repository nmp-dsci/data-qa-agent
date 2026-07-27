import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export type KitSelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

// Radix Select forbids item value="" (it's reserved for "no selection"), but
// several filters use "" to mean "all" — map it through a sentinel so callers
// keep their existing state shape.
//
// The sentinel is only substituted when the caller actually offers a ""
// option. Otherwise "" means "nothing chosen yet" and must reach Radix as ""
// — swapping in the sentinel there selects a value no item carries, so the
// trigger renders blank and the placeholder never appears (that is exactly
// what happened to Explore's "+ filter" control).
const EMPTY = "__kit_empty__";

type Props = {
  value: string;
  onValueChange: (value: string) => void;
  options: KitSelectOption[];
  placeholder?: string;
  ariaLabel?: string;
  id?: string;
  /** Mirrors the old `data-testid` on the native <select>: it lands on the
   *  trigger, so e2e keeps its handle (assert on text now, not value). */
  testId?: string;
  title?: string;
  disabled?: boolean;
  size?: "sm" | "default";
  className?: string;
  contentClassName?: string;
};

export function KitSelect({
  value,
  onValueChange,
  options,
  placeholder,
  ariaLabel,
  id,
  testId,
  title,
  disabled,
  size = "sm",
  className,
  contentClassName,
}: Props) {
  const hasEmptyOption = options.some((o) => o.value === "");
  return (
    <Select
      value={value === "" && hasEmptyOption ? EMPTY : value}
      onValueChange={(v) => onValueChange(v === EMPTY ? "" : v)}
      disabled={disabled}
    >
      {/* data-value on the trigger and every option is the migration's e2e
          contract: a Radix select has no .value to assert on and its options
          are labelled, not valued, so specs would otherwise have to know every
          human label. With these, `toHaveAttribute("data-value", …)` and
          `[role=option][data-value=…]` replace the old toHaveValue() /
          selectOption() one-for-one. */}
      <SelectTrigger
        id={id}
        data-testid={testId}
        data-value={value}
        title={title}
        aria-label={ariaLabel}
        size={size}
        className={cn("min-w-0", className)}
      >
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent className={contentClassName}>
        {options.map((o) => (
          <SelectItem
            key={o.value}
            value={o.value === "" ? EMPTY : o.value}
            data-value={o.value}
            disabled={o.disabled}
          >
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
