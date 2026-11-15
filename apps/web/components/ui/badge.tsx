// Adapted from ShadowAstra_Atlas (MIT, Team ShadowAstra), see THIRD_PARTY_NOTICES.md.
// shadcn/ui Badge, radix-nova style (shadcn 4.23 registry, MIT), adapted for Terraluna (PRD 8.2,
// 8.10). A server component: Slot comes from @radix-ui/react-slot, which has no client directive.
// Changes from the registry copy:
// - no focus ring classes: the 2px outline from app/globals.css shows on a focused badge link;
// - extra variants for the PRD 3 evidence labels and the PRD 9 gate statuses: each is a tinted
//   fill, a coloured edge and the literal text, and every one has its own edge style or glyph
//   so none relies on colour alone;
// - no dark-mode or icon classes: the app has one dark theme;
// - colour transitions only, switched off under prefers-reduced-motion;
// - the link variant is always underlined (WCAG 1.4.1); other variants are not, even on <a>.
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex h-6 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap no-underline transition-colors motion-reduce:transition-none',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground [a]:hover:bg-primary/80',
        secondary: 'bg-secondary text-secondary-foreground [a]:hover:bg-accent',
        outline: 'border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground',
        ghost: 'hover:bg-muted hover:text-muted-foreground',
        link: 'text-primary underline underline-offset-4',
        destructive: 'border-destructive bg-destructive/15 text-destructive',
        // Evidence labels (PRD 3): edge style differs per label.
        // Observed is the one solid fill, so it differs from Derived (same solid edge) in greyscale.
        observed: 'border-solid border-evidence-observed bg-evidence-observed text-background',
        derived: 'border-solid border-evidence-derived bg-evidence-derived/15 text-evidence-derived',
        predicted: 'border-dashed border-evidence-predicted bg-evidence-predicted/15 text-evidence-predicted',
        assumed: 'border-dotted border-evidence-assumed bg-evidence-assumed/15 text-evidence-assumed',
        unavailable: 'border-double border-evidence-unavailable bg-evidence-unavailable/10 text-evidence-unavailable',
        // Gate statuses (PRD 9).
        pass: 'border-status-pass bg-status-pass/15 text-status-pass',
        fail: 'border-status-fail bg-status-fail/15 text-status-fail',
        partial: 'border-dashed border-status-partial bg-status-partial/15 text-status-partial',
        'not-run': 'border-dotted border-status-not-run bg-status-not-run/10 text-status-not-run',
        'not-applicable': 'border-double border-status-not-applicable bg-status-not-applicable/10 text-status-not-applicable',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

function Badge({
  className,
  variant = 'default',
  asChild = false,
  ...props
}: ComponentProps<'span'> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'span';

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
