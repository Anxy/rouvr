"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AnimatePresence, m } from "motion/react";
import { Check, ChevronDown } from "lucide-react";
import { fieldClass } from "@/components/forms/FieldError";
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
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
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
