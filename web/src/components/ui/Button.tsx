import Link from "next/link";
import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost";
type Size = "md" | "lg";

const base =
  "group relative inline-flex items-center justify-center gap-2 rounded-full font-medium transition-[transform,background-color,color,border-color] duration-300 ease-out active:scale-[0.98] disabled:pointer-events-none disabled:opacity-60";

const variants: Record<Variant, string> = {
  primary:
    "bg-brand text-abyss hover:bg-brand-300 shadow-[0_0_0_1px_rgb(8_169_240/0.4),0_8px_30px_-6px_rgb(8_169_240/0.6)]",
  secondary:
    "glass text-cloud hover:border-brand/60 hover:text-white",
  ghost: "text-muted hover:text-white",
};

const sizes: Record<Size, string> = {
  md: "h-10 px-5 text-sm",
  lg: "h-12 px-7 text-[15px]",
};

type CommonProps = {
  variant?: Variant;
  size?: Size;
  className?: string;
  children: React.ReactNode;
};

type ButtonAsLink = CommonProps & { href: string } & Omit<
    React.ComponentProps<typeof Link>,
    "href" | "className" | "children"
  >;
type ButtonAsButton = CommonProps & { href?: undefined } & Omit<
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    "className" | "children"
  >;

export function Button(props: ButtonAsLink | ButtonAsButton) {
  const { variant = "primary", size = "md", className, children, ...rest } = props;
  const classes = cn(base, variants[variant], sizes[size], className);

  if (rest.href !== undefined) {
    return (
      <Link {...(rest as ButtonAsLink)} className={classes}>
        {children}
      </Link>
    );
  }
  return (
    <button {...(rest as ButtonAsButton)} className={classes}>
      {children}
    </button>
  );
}
