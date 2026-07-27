// KitMultiSelect — the multi-value companion to KitSelect. Radix's Select is
// single-value by design, so the shadcn idiom for "pick several" is a Popover
// wrapping a Command list with a tick per row; that is exactly what this is,
// built on the two primitives already vendored in components/ui.
//
// It replaces `<select multiple>`, whose cmd-click model almost nobody
// discovers and which cannot show a summary of what is chosen. Selection order
// is preserved (click order), because callers like the grader's composite key
// treat the array as ordered.
import { Check, ChevronsUpDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type KitMultiOption = { value: string; label: string };

export function KitMultiSelect({
  values,
  onValuesChange,
  options,
  placeholder = "— none —",
  ariaLabel,
  testId,
  searchPlaceholder = "Filter…",
  className,
  disabled,
}: {
  values: string[];
  onValuesChange: (values: string[]) => void;
  options: KitMultiOption[];
  placeholder?: string;
  ariaLabel?: string;
  testId?: string;
  searchPlaceholder?: string;
  className?: string;
  disabled?: boolean;
}) {
  const labelFor = (v: string) => options.find((o) => o.value === v)?.label ?? v;
  // The trigger states the selection in full rather than "3 selected": these
  // lists are short, and which columns are keyed is the whole question.
  const summary = values.length ? values.map(labelFor).join(", ") : placeholder;

  function toggle(value: string) {
    onValuesChange(
      values.includes(value) ? values.filter((v) => v !== value) : [...values, value],
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          role="combobox"
          disabled={disabled}
          aria-label={ariaLabel}
          data-testid={testId}
          // Same e2e contract as KitSelect: the chosen values, in order, as one
          // comma-joined attribute — the replacement for toHaveValues().
          data-value={values.join(",")}
          className={cn("justify-between font-normal", className)}
        >
          <span className={cn("truncate", !values.length && "text-muted-foreground")}>
            {summary}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] min-w-52 p-0" align="start">
        <Command>
          <CommandInput placeholder={searchPlaceholder} className="h-9" />
          <CommandList>
            <CommandEmpty>No matches.</CommandEmpty>
            <CommandGroup>
              {options.map((o) => (
                <CommandItem
                  key={o.value}
                  value={o.label}
                  // NOT data-value — cmdk owns that attribute on its items (it
                  // holds the search key). data-option-value is ours.
                  data-option-value={o.value}
                  onSelect={() => toggle(o.value)}
                >
                  <Check
                    className={cn("size-4", values.includes(o.value) ? "opacity-100" : "opacity-0")}
                  />
                  {o.label}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
