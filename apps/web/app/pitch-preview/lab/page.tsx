// /pitch-preview/lab: Figure 8 (the Lab terrain twins) full width on the deck's stage colour, for review.
import { FigLabClient } from '@/components/pitch/lab/fig-lab-client';
import { buildPitchData } from '@/components/pitch/pitch-data';
import { loadBundle } from '@/lib/bundle';

export default function LabPreviewPage() {
  const { lab } = buildPitchData(loadBundle());
  return (
    <main className="min-h-screen bg-[#F9F8F5] px-10 py-10">
      <div className="h-[760px] w-full">{lab === null ? <p className="text-sm text-[#6B645C]">Lab site unavailable in this bundle.</p> : <FigLabClient {...lab} />}</div>
    </main>
  );
}
