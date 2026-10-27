"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { AnimatePresence, m } from "motion/react";
import { ArrowUpRight, Loader2 } from "lucide-react";
import { Mascot } from "@/components/brand/Mascot";
import { Button } from "@/components/ui/Button";
import { contactSchema, type ContactInput } from "@/lib/validations";
import { cn } from "@/lib/utils";
import { FieldError, fieldClass } from "./FieldError";

export function ContactForm() {
  const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ContactInput>({
    resolver: zodResolver(contactSchema),
    defaultValues: { name: "", email: "", company: "", message: "", website: "" },
  });

  const onSubmit = async (values: ContactInput) => {
    setServerError(null);
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      const data = (await res.json()) as { ok: boolean; error?: string };
      if (!res.ok || !data.ok) throw new Error(data.error ?? "Something went wrong.");
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setServerError(err instanceof Error ? err.message : "Something went wrong.");
    }
  };

  return (
    <AnimatePresence mode="wait" initial={false}>
      {status === "success" ? (
        <m.div
          key="success"
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
          className="flex flex-col items-center py-10 text-center"
          role="status"
        >
          <Mascot state="done" className="size-24 drop-shadow-[0_10px_30px_rgb(8_169_240/0.5)]" />
          <h2 className="mt-6 text-2xl font-semibold text-white">Thanks for sharing what you have in mind.</h2>
          <p className="mt-3 max-w-sm text-muted">We&apos;ll be in touch.</p>
        </m.div>
      ) : (
        <m.form
          key="form"
          exit={{ opacity: 0, y: -10 }}
          onSubmit={handleSubmit(onSubmit)}
          noValidate
          className="space-y-5"
        >
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor="contact-name" className="mb-2 block text-sm font-medium text-cloud">
                Name <span className="text-brand">*</span>
              </label>
              <input
                id="contact-name"
                type="text"
                autoComplete="name"
                aria-invalid={!!errors.name}
                aria-describedby={errors.name ? "contact-name-error" : undefined}
                className={cn(fieldClass, errors.name && "border-danger/70")}
                {...register("name")}
              />
              <FieldError id="contact-name-error" message={errors.name?.message} />
            </div>
            <div>
              <label htmlFor="contact-email" className="mb-2 block text-sm font-medium text-cloud">
                Email <span className="text-brand">*</span>
              </label>
              <input
                id="contact-email"
                type="email"
                autoComplete="email"
                aria-invalid={!!errors.email}
                aria-describedby={errors.email ? "contact-email-error" : undefined}
                className={cn(fieldClass, errors.email && "border-danger/70")}
                {...register("email")}
              />
              <FieldError id="contact-email-error" message={errors.email?.message} />
            </div>
          </div>

          <div>
            <label htmlFor="contact-company" className="mb-2 block text-sm font-medium text-cloud">
              Role or organisation <span className="text-muted">(optional)</span>
            </label>
            <input
              id="contact-company"
              type="text"
              autoComplete="organization"
              className={fieldClass}
              {...register("company")}
            />
          </div>

          <div>
            <label htmlFor="contact-message" className="mb-2 block text-sm font-medium text-cloud">
              What would you hand over to rouvr first? <span className="text-brand">*</span>
            </label>
            <textarea
              id="contact-message"
              rows={5}
              placeholder="A client build, a weekly report, the project you keep putting off…"
              aria-invalid={!!errors.message}
              aria-describedby={errors.message ? "contact-message-error" : undefined}
              className={cn(fieldClass, "resize-y", errors.message && "border-danger/70")}
              {...register("message")}
            />
            <FieldError id="contact-message-error" message={errors.message?.message} />
          </div>

          <div className="absolute -left-[9999px] h-0 w-0 overflow-hidden" aria-hidden>
            <label htmlFor="contact-website">Website</label>
            <input id="contact-website" tabIndex={-1} autoComplete="off" {...register("website")} />
          </div>

          {status === "error" && serverError && (
            <p role="alert" className="rounded-lg border border-danger/40 bg-danger/10 px-4 py-3 text-sm text-danger">
              {serverError}
            </p>
          )}

          <Button type="submit" size="lg" disabled={isSubmitting} className="w-full">
            {isSubmitting ? (
              <>
                <Loader2 className="size-4 animate-spin" /> Sending
              </>
            ) : (
              <>
                Get in touch
                <ArrowUpRight className="size-4 transition-transform duration-300 group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </>
            )}
          </Button>
        </m.form>
      )}
    </AnimatePresence>
  );
}
