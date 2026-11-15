import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "cn"
import { Slot } from "radix-ui"

const badgeVariants = cva(
  "group/badge inline-flex h-6 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 has-data-[icon=inline-end]:pr-1.5 has-data-[icon=inline-start]:pl-1.5 aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 [&>svg]:pointer-events-none [&>svg]:size-2.5!",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a]:hover:bg-primary/80",
        secondary:
          "bg-secondary text-secondary-foreground [a]:hover:bg-secondary/80",
        destructive:
          "bg-destructive/10 text-destructive focus-visible:ring-destructive/20 dark:bg-destructive/20 dark:focus-visible:ring-destructive/40 [a]:hover:bg-destructive/20",
        outline:
          "border-border bg-input/20 text-foreground dark:bg-input/30 [a]:hover:bg-muted [a]:hover:text-muted-foreground",
        ghost:
          "hover:bg-muted hover:text-muted-foreground dark:hover:bg-muted/50",
        link: "text-primary underline-offset-4 hover:underline",
        // Evidence labels (PRD 3) and gate statuses (PRD 9); edge style differs so none relies on colour.
        observed: "border-solid border-evidence-observed bg-evidence-observed text-background",
        derived: "border-solid border-evidence-derived bg-evidence-derived/15 text-evidence-derived",
        predicted: "border-dashed border-evidence-predicted bg-evidence-predicted/15 text-evidence-predicted",
        assumed: "border-dotted border-evidence-assumed bg-evidence-assumed/15 text-evidence-assumed",
        unavailable: "border-double border-evidence-unavailable bg-evidence-unavailable/10 text-evidence-unavailable",
        pass: "border-status-pass bg-status-pass/15 text-status-pass",
        fail: "border-status-fail bg-status-fail/15 text-status-fail",
        partial: "border-dashed border-status-partial bg-status-partial/15 text-status-partial",
        "not-run": "border-dotted border-status-not-run bg-status-not-run/10 text-status-not-run",
        "not-applicable": "border-double border-status-not-applicable bg-status-not-applicable/10 text-status-not-applicable",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
