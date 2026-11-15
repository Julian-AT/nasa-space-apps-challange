'use client';

// The one-page summary slide (/pitch-slide): a fixed 1920x1080 artboard, scaled to fit the window,
// rendered as a full-screen overlay above the site shell. All values arrive from the server page,
// read from the release bundle and numbers.tex; a missing value shows as Unavailable, never zero.
import { HugeiconsIcon } from '@hugeicons/react';
import { Location01Icon, Pdf01Icon, Route01Icon } from '@hugeicons/core-free-icons';
import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';

const EarthHero = dynamic(() => import('./earth-hero'), { ssr: false, loading: () => null });

export interface OneSlideData {
  readonly bundleVersion: string;
  readonly mock: boolean;
  readonly shippingModes: readonly string[];
  readonly jezeroBest: number | null;
  readonly jezeroEarth: string | null;
  readonly jezeroLabel: string | null;
  readonly cardCount: number;
  readonly parameters: number | null;
  readonly parity: string | null;
  readonly parityTolerance: number | null;
  readonly lagMin: number | null;
  readonly lagMax: number | null;
  readonly windowM: number | null;
  /** e.g. "ResNet-18 layout", from the model card's architecture string. */
  readonly archFamily: string | null;
  readonly embeddingDim: number | null;
  readonly inputPx: number | null;
  /** Sum of the model card's train window counts over bodies. */
  readonly trainWindows: number | null;
}

export interface QrLink {
  readonly label: string;
  readonly url: string;
  readonly svg: string;
}

const W = 1920;
const H = 1080;
const PAPER = '#F9F8F5';
const INK = '#1A1613';
const SECOND = '#4D4843';
const MUTED = '#A39E95';
const ACCENT = '#CC5800';
const GREEN = '#4A8044';
const BLUE = '#7AA7C7';
const HAIR = 'rgba(26,22,19,0.10)';
const SANS = 'var(--pitch-sans), ui-sans-serif, system-ui, sans-serif';
const MONO = 'var(--pitch-mono), ui-monospace, monospace';
const NA = 'Unavailable';


function TechCard({ index, title, lines }: { index: string; title: string; lines: readonly string[] }) {
  return (
    <div style={{ border: `1px solid ${HAIR}`, borderRadius: 10, padding: '20px 22px 22px', background: '#FFFFFF', display: 'flex', flexDirection: 'column' }}>
      <div style={{ fontFamily: MONO, fontSize: 14, color: MUTED, letterSpacing: '0.04em' }}>{index}</div>
      <div style={{ fontSize: 24, fontWeight: 500, letterSpacing: '-0.01em', lineHeight: 1.15, color: INK, marginTop: 10 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 10 }}>
        {lines.map((l) => (
          <div key={l} style={{ fontSize: 17, lineHeight: 1.35, color: SECOND }}>
            {l}
          </div>
        ))}
      </div>
    </div>
  );
}

function Feature({ icon, color, text }: { icon: typeof Location01Icon; color: string; text: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontSize: 22, color: SECOND, lineHeight: 1.3 }}>
      <span style={{ display: 'inline-flex', width: 36, height: 36, borderRadius: 10, border: `1px solid ${HAIR}`, alignItems: 'center', justifyContent: 'center', flex: 'none', background: '#FFFFFF' }}>
        <HugeiconsIcon icon={icon} size={20} color={color} strokeWidth={1.6} />
      </span>
      {text}
    </div>
  );
}

export function OneSlide({ data, qr, fonts }: { data: OneSlideData; qr: readonly QrLink[]; fonts: string }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [scale, setScale] = useState(1);
  useEffect(() => {
    setHost(document.body);
    const fit = () => {
      setScale(Math.min(window.innerWidth / W, window.innerHeight / H));
    };
    fit();
    window.addEventListener('resize', fit);
    return () => {
      window.removeEventListener('resize', fit);
    };
  }, []);

  const fmtInt = (v: number): string => v.toLocaleString('en-US');
  const lagLine = data.lagMin === null || data.lagMax === null ? `Lags ${NA}` : `Lags ${String(data.lagMin)} to ${String(data.lagMax)} m`;
  const winLine = data.windowM === null ? '' : `, ${String(data.windowM / 1000)} km windows`;
  const params = data.parameters === null ? `${NA} parameters` : `${(data.parameters / 1e6).toFixed(1)}M parameters`;
  // 3 input channels: the encoder stem, nn.Conv2d(3, ...) in ml/src/terraluna_ml/model.py.
  const input = data.inputPx === null ? `input ${NA}` : `${String(data.inputPx)}\u00a0x\u00a0${String(data.inputPx)}\u00a0x\u00a03 input`;
  const emb = data.embeddingDim === null ? `embedding ${NA}` : `${String(data.embeddingDim)}-d embedding`;
  const train = data.trainWindows === null ? `Training windows ${NA}` : `${fmtInt(data.trainWindows)} windows from Moon, Mars and Earth`;
  const cards = [
    { title: 'Physics fingerprint', lines: ['Structure function nu(L), relief and texture', lagLine + winLine] },
    { title: 'Terrain encoder', lines: [`${data.archFamily ?? 'ResNet'}, ${params}`, `${emb}, ${input}`] },
    { title: 'Self-supervised training', lines: ['VICReg, physics head, gradient-reversal planet adversary', train] },
    { title: 'In the browser', lines: [`ONNX on WebAssembly, parity ${data.parity ?? NA}`, 'Live probe of any point on Earth'] },
  ];

  if (host === null) return <div className="fixed inset-0 z-[1000]" style={{ background: PAPER }} />;
  return createPortal(
    <div className={`fixed inset-0 z-[1000] overflow-hidden antialiased ${fonts}`} style={{ background: PAPER, color: INK, fontFamily: SANS }} aria-label="Terraluna summary slide" role="region">
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          width: W,
          height: H,
          transform: `translate(-50%, -50%) scale(${String(scale)})`,
          transformOrigin: 'center',
          background: PAPER,
        }}
      >
        {data.mock && (
          <div role="alert" style={{ position: 'absolute', insetInline: 0, top: 0, zIndex: 20, background: '#FEF3C7', color: '#78350F', padding: '10px 24px', textAlign: 'center', fontSize: 18, fontWeight: 500 }}>
            Mock preview: fixture bundle, not production data
          </div>
        )}

        {/* left two thirds */}
        <div style={{ position: 'absolute', left: 120, top: 88, width: 1130, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: 22, color: MUTED, letterSpacing: '0.01em', display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 8, height: 8, borderRadius: 8, background: ACCENT, display: 'inline-block' }} />
            NASA Space Apps 2026, Team Terraluna
          </div>
          <h1 style={{ fontSize: 112, fontWeight: 500, letterSpacing: '-0.03em', lineHeight: 1, margin: '30px 0 0' }}>Terraluna</h1>
          <p style={{ fontSize: 44, fontWeight: 500, letterSpacing: '-0.01em', lineHeight: 1.15, margin: '22px 0 0', color: INK }}>
            Find the Moon and Mars on Earth.
          </p>
          <p style={{ fontSize: 26, lineHeight: 1.45, margin: '26px 0 0', color: SECOND, maxWidth: 1000 }}>
            A physics terrain fingerprint and a self-supervised terrain encoder that compare any place on Earth with lunar and Martian landing sites, live in
            the browser.
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14, marginTop: 44 }}>
            {cards.map((c, k) => (
              <TechCard key={c.title} index={String(k + 1).padStart(2, '0')} title={c.title} lines={c.lines} />
            ))}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 34 }}>
            <Feature icon={Location01Icon} color={ACCENT} text="Live finder: any point on Earth in seconds" />
            <Feature icon={Route01Icon} color={GREEN} text="Lab: same rover route on real Earth and Mars elevation" />
            <Feature icon={Pdf01Icon} color={BLUE} text="Paper with benchmarks" />
          </div>
        </div>

        {/* right third: Earth and QR codes */}
        <div style={{ position: 'absolute', left: 1280, top: 64, width: 560, height: 600 }}>
          <EarthHero className="absolute inset-0" />
        </div>
        <div style={{ position: 'absolute', left: 1300, top: 712, width: 520, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 20 }}>
          {qr.map((l) => (
            <div key={l.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
              <div style={{ width: 160, height: 160, padding: 14, borderRadius: 10, border: `1px solid ${HAIR}`, background: '#FFFFFF', boxSizing: 'border-box' }}>
                {l.svg === '' ? (
                  <div style={{ fontSize: 14, color: MUTED }}>{NA}</div>
                ) : (
                  <div className="[&_svg]:block [&_svg]:size-full" style={{ width: '100%', height: '100%' }} dangerouslySetInnerHTML={{ __html: l.svg }} />
                )}
              </div>
              <div style={{ fontSize: 20, fontWeight: 500, color: INK }}>{l.label}</div>
            </div>
          ))}
        </div>

        {/* footer: provenance and claim limits */}
        <div
          style={{
            position: 'absolute',
            left: 120,
            right: 80,
            bottom: 48,
            paddingTop: 18,
            borderTop: `1px solid ${HAIR}`,
            display: 'flex',
            justifyContent: 'space-between',
            fontSize: 15,
            color: MUTED,
          }}
        >
          <span>Terrain fingerprint distances from NASA and ESA elevation data.</span>
          <span>
            nasa.julianschmidt.cv<span style={{ fontFamily: MONO, marginLeft: 16 }}>bundle {data.bundleVersion}</span>
          </span>
        </div>
      </div>
    </div>,
    host,
  );
}
