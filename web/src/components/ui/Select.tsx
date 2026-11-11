"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import { fieldClass } from "@/components/forms/FieldError";
import { filterComboboxOptions } from "@/lib/combobox";
import { cn } from "@/lib/utils";

type Option = { value: string; label: string };

export function Select({
  id,
  name,
  value,
  onChange,
  onBlur,
  options,
  placeholder = "Select",
  invalid,
  describedBy,
}: {
  id: string;
  name?: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  options: readonly Option[];
  placeholder?: string;
  invalid?: boolean;
  describedBy?: string;
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        onBlur?.();
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActive((index) => Math.min(options.length - 1, index + 1));
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActive((index) => Math.max(0, index - 1));
      }
      if (event.key === "Enter") {
        event.preventDefault();
        const option = options[active];
        if (option) onChange(option.value);
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, active, options, onChange, onBlur]);

  const openList = () => {
    const current = Math.max(0, options.findIndex((option) => option.value === value));
    setActive(current);
    setOpen(true);
  };

  return (
    <div ref={rootRef} className={cn("relative", open && "z-30")}>
      {/* aria-invalid is global in ARIA 1.2; jsx-a11y still omits it for buttons. */}
      {/* eslint-disable-next-line jsx-a11y/role-supports-aria-props */}
      <button
        id={id}
        name={name}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) openList();
          }
        }}
        className={cn(
          fieldClass,
          "flex items-center justify-between gap-3 text-left",
          !selected && "text-muted/60",
          invalid && "border-danger/70",
          open && "border-brand ring-4 ring-brand/15",
        )}
      >
        <span className="truncate">{selected?.label ?? placeholder}</span>
        <ChevronDown
          className={cn("size-4 shrink-0 text-muted transition-transform duration-200", open && "rotate-180 text-brand-300")}
          aria-hidden
        />
      </button>

      <AnimatePresence>
        {open && (
          <m.ul
            id={listId}
            role="listbox"
            aria-labelledby={id}
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{
              opacity: 0,
              scale: 0.97,
              transition: { duration: 0.12, ease: [0.23, 1, 0.32, 1] },
            }}
            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
            style={{ transformOrigin: "top center" }}
            className="absolute left-0 right-0 z-30 mt-2 overflow-hidden rounded-xl border border-steel/70 bg-navy p-1.5 shadow-[0_24px_60px_-24px_rgb(8_169_240/0.45)]"
          >
            {options.map((option, index) => {
              const isSelected = option.value === value;
              const isActive = index === active;
              return (
                <li key={option.value} role="presentation">
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onMouseEnter={() => setActive(index)}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      onChange(option.value);
                      setOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-[15px] transition-colors",
                      isActive || isSelected ? "bg-brand/10 text-white" : "text-cloud hover:bg-white/[0.04]",
                    )}
                  >
                    <span>{option.label}</span>
                    {isSelected && <Check className="size-4 shrink-0 text-brand-300" aria-hidden />}
                  </button>
                </li>
              );
            })}
          </m.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

// Combobox extends the Select listbox pattern with a free-text input:
// typeahead substring filtering, ArrowUp/ArrowDown + Enter + Escape
// keyboard nav, and custom values (project pickers accept new names).
// No new deps — same theme tokens, motion dropdown, and steel/brand
// styling as Select. The dropdown scrolls internally so 10+ projects
// stay usable.
//
// Filtering only kicks in after the user types while the list is open.
// Using the selected value itself as the query (e.g. "All projects")
// would hide every other option the moment the list opens.
export function Combobox({
  id,
  value,
  onChange,
  onBlur,
  options,
  placeholder = "Select",
  ariaLabel,
  inputClassName,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  onBlur?: () => void;
  options: readonly string[];
  placeholder?: string;
  ariaLabel: string;
  inputClassName?: string;
}) {
  const listId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [filtering, setFiltering] = useState(false);

  const filtered = useMemo(
    () => filterComboboxOptions(options, value, filtering),
    [options, value, filtering],
  );

  // safeActive clamps the highlight to a real row, so an externally
  // reset value can never strand the highlight out of range.
  const safeActive = filtered.length === 0 ? 0 : Math.min(active, filtered.length - 1);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setFiltering(false);
        onBlur?.();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open, onBlur]);

  const choose = (option: string) => {
    onChange(option);
    setFiltering(false);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className={cn("relative", open && "z-30")}>
      <input
        id={id}
        value={value}
        onChange={(e) => {
          setFiltering(true);
          onChange(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => {
          setFiltering(false);
          setOpen(true);
        }}
        onBlur={onBlur}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setOpen(true);
            setActive((index) => Math.min(filtered.length - 1, index + 1));
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            setActive((index) => Math.max(0, index - 1));
          } else if (event.key === "Enter") {
            const option = filtered[safeActive];
            if (open && option) {
              event.preventDefault();
              choose(option);
            } else {
              setOpen(false);
            }
          } else if (event.key === "Escape") {
            setOpen(false);
          }
        }}
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && filtered.length > 0 ? `${listId}-${safeActive}` : undefined}
        aria-autocomplete="list"
        aria-label={ariaLabel}
        placeholder={placeholder}
        autoComplete="off"
        className={
          inputClassName ??
          "w-full rounded-xl border border-steel/40 bg-abyss/60 px-3 py-2 text-sm text-cloud placeholder:text-muted/60 focus:border-brand/60 focus:outline-none"
        }
      />
      <button
        type="button"
        tabIndex={-1}
        aria-hidden
        onClick={() => setOpen((v) => !v)}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted transition-colors hover:text-cloud"
      >
        <ChevronDown
          className={cn("size-4 transition-transform duration-200", open && "rotate-180 text-brand-300")}
        />
      </button>

      <AnimatePresence>
        {open && (
          <m.ul
            id={listId}
            role="listbox"
            aria-label={ariaLabel}
            initial={{ opacity: 0, scale: 0.97 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{
              opacity: 0,
              scale: 0.97,
              transition: { duration: 0.12, ease: [0.23, 1, 0.32, 1] },
            }}
            transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
            style={{ transformOrigin: "top center" }}
            className="absolute left-0 right-0 z-30 mt-2 max-h-60 overflow-y-auto rounded-xl border border-steel/70 bg-navy p-1.5 shadow-[0_24px_60px_-24px_rgb(8_169_240/0.45)]"
          >
            {filtered.length === 0 ? (
              <li role="presentation" className="px-3 py-2.5 text-left text-sm text-muted/60">
                No matches — press Enter to keep what you typed.
              </li>
            ) : (
              filtered.map((option, index) => {
                const isActive = index === safeActive;
                const isSelected = option === value;
                return (
                  <li key={option} id={`${listId}-${index}`} role="presentation">
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onMouseEnter={() => setActive(index)}
                      onMouseDown={(event) => {
                        event.preventDefault();
                        choose(option);
                      }}
                      className={cn(
                        "flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left text-[15px] transition-colors",
                        isActive || isSelected ? "bg-brand/10 text-white" : "text-cloud hover:bg-white/[0.04]",
                      )}
                    >
                      <span className="truncate">{option}</span>
                      {isSelected && <Check className="size-4 shrink-0 text-brand-300" aria-hidden />}
                    </button>
                  </li>
                );
              })
            )}
          </m.ul>
        )}
      </AnimatePresence>
    </div>
  );
}
