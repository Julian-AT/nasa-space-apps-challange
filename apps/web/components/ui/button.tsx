// shadcn/ui Button, radix-nova style (shadcn 4.23 registry, MIT), adapted for Terraluna (PRD 8.2,
// 8.10). A server component: Slot comes from @radix-ui/react-slot, which has no client directive.
// Changes from the registry copy:
// - nothing that removes the outline: the 2px outline from app/globals.css shows on focus;
// - no destructive variant or aria-invalid styles: red is reserved for the mock banner;
// - no dark-mode, icon or button-group classes: the app has one light theme and no icons;
// - colour transitions only, switched off under prefers-reduced-motion;
// - the link variant is always underlined (WCAG 1.4.1); other variants are not, even on <a>.
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full border border-transparent bg-clip-padding text-sm font-medium whitespace-nowrap no-underline transition-colors select-none motion-reduce:transition-none disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/80',
        outline:
          'border-border bg-background hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground',
        secondary:
          'bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground aria-expanded:bg-secondary aria-expanded:text-secondary-foreground',
        ghost:
          'hover:bg-muted hover:text-foreground aria-expanded:bg-muted aria-expanded:text-foreground',
        link: 'text-primary underline underline-offset-4',
      },
      size: {
        default: 'h-9 px-4',
        xs: 'h-6 gap-1 px-2.5 text-xs',
        sm: 'h-8 gap-1 px-3 text-[0.8rem]',
        lg: 'h-11 px-6',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

function Button({
  className,
  variant = 'default',
  size = 'default',
  asChild = false,
  ...props
}: ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'button';

  return (
    <Comp
      data-slot="button"
      data-variant={variant}
      data-size={size}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
