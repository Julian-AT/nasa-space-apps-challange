// shadcn/ui Badge, radix-nova style (shadcn 4.23 registry, MIT), adapted for Terraluna (PRD 8.2,
// 8.10). A server component: Slot comes from @radix-ui/react-slot, which has no client directive.
// Changes from the registry copy:
// - no focus ring classes: the 2px outline from app/globals.css shows on a focused badge link;
// - no destructive variant or aria-invalid styles: red is reserved for the mock banner;
// - no dark-mode or icon classes: the app has one light theme and no icons;
// - colour transitions only, switched off under prefers-reduced-motion;
// - the link variant is always underlined (WCAG 1.4.1); other variants are not, even on <a>.
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-4xl border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap no-underline transition-colors motion-reduce:transition-none',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground [a]:hover:bg-primary/80',
        secondary: 'bg-secondary text-secondary-foreground [a]:hover:bg-accent',
        outline: 'border-border text-foreground [a]:hover:bg-muted [a]:hover:text-muted-foreground',
        ghost: 'hover:bg-muted hover:text-muted-foreground',
        link: 'text-primary underline underline-offset-4',
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
