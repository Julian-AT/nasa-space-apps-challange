// The card's Open Graph image: text and shapes only, from ogText (PRD 10, D08).
import { ImageResponse } from 'next/og';

import { ogText } from '../../../components/card/og-text.ts';
import { loadBundle } from '../../../lib/bundle.ts';

export const alt = 'Terraluna evidence card';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';
export const dynamicParams = false;

export function generateStaticParams(): { cardId: string }[] {
  return [...loadBundle().records.cards.keys()].map((cardId) => ({ cardId }));
}

export default async function Image({ params }: { params: Promise<{ cardId: string }> }) {
  const { cardId } = await params;
  const bundle = loadBundle();
  const card = bundle.records.cards.get(cardId);
  if (card === undefined) throw new Error(`no card ${cardId} in the bundle`);
  const { lines, mock } = ogText(card, bundle.manifest);
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: 24,
          padding: 72,
          background: '#0b1020',
          color: '#e8ecf5',
          fontSize: 36,
          borderLeft: `24px solid ${mock ? '#d12c2c' : '#6aa8ff'}`,
        }}
      >
        {lines.map((line, i) => (
          <div key={line} style={{ display: 'flex', fontSize: i === 0 ? 56 : 34, fontWeight: i === 0 ? 700 : 400 }}>
            {line}
          </div>
        ))}
      </div>
    ),
    size,
  );
}
