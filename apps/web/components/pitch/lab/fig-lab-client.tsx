'use client';

// Client-only loader for Figure 8, so server pages can mount it without pulling three into SSR.
import dynamic from 'next/dynamic';

import type { FigLabProps } from '@/components/pitch/fig-lab';

const FigLab = dynamic(() => import('@/components/pitch/fig-lab'), { ssr: false, loading: () => <div className="size-full bg-[#F9F8F5]" /> });

export function FigLabClient(props: FigLabProps) {
  return <FigLab {...props} />;
}
