// Speaker notes for /pitch: the two-minute script, verbatim per slide (**bold** = stress), with each
// slide's planned window in seconds. The presenter drawer (N) and the pace bar read these.
export interface SlideNote {
  readonly title: string;
  readonly from: number;
  readonly to: number;
  readonly text: string;
}

export const TOTAL_S = 120;

export const NOTES: readonly SlideNote[] = [
  {
    title: 'Title',
    from: 0,
    to: 8,
    text: 'Before anyone lands on the Moon or Mars, crews and rovers **practise on Earth**. Terraluna finds out **where**.',
  },
  {
    title: 'Problem',
    from: 8,
    to: 20,
    text: 'Today, analog sites are chosen by **experience**: a handful of known places, described in field reports. We **measure** them instead, for **any point on Earth**, and back every match with **evidence**.',
  },
  {
    title: 'Physics fingerprint',
    from: 20,
    to: 34,
    text: 'We compute a **physics fingerprint**: how much the ground rises and falls from **270 metres to a kilometre**, for every five-kilometre window. Each dot is one Earth window. For **Jezero crater**, the best match is **15 times closer** than a typical window.',
  },
  {
    title: 'Live finder',
    from: 34,
    to: 48,
    text: 'And it runs **live**. (Right arrow.) We zoom into **Rosamond Dry Lake** in California, fetch **real elevation**, compute the fingerprint **right here in the browser**, and compare it with Jezero. It is closer than **91 percent** of all the windows we searched.',
  },
  {
    title: 'Our model: architecture',
    from: 48,
    to: 68,
    text: 'On top of that we trained **our own model**. (Right arrow for each step.) One five-kilometre window. **Two augmented views** of it. **One shared ResNet encoder**. **VICReg** makes both views agree. A **physics head** learns the fingerprint. And a **gradient-reversal adversary** pushes the model to ignore **which planet** the terrain comes from. (Last step.) Here is the full architecture.',
  },
  {
    title: 'Training',
    from: 68,
    to: 76,
    text: 'Trained on **Moon, Mars and Earth** windows: the self-supervised loss falls, and the **physics error drops tenfold**.',
  },
  {
    title: 'Benchmarks',
    from: 76,
    to: 84,
    text: 'It trains **34 times faster** on a laptop GPU than on our server, and runs **in the browser** with **0.99999** agreement to the training model.',
  },
  {
    title: 'The Lab',
    from: 84,
    to: 102,
    text: 'Then the **Lab**: real elevation of **Holuhraun in Iceland** and **Jezero crater**, side by side **at the same scale**. A rover drives **the same route** on both, and you see **slope, climb and descent** live.',
  },
  {
    title: 'Results',
    from: 102,
    to: 114,
    text: 'The result is **seven evidence cards**. In both regions we searched, **Iceland and California**, our best matches for Jezero and Arcadia are **many times closer** than a typical window.',
  },
  {
    title: 'Close',
    from: 114,
    to: 120,
    text: 'Terraluna: **find the Moon and Mars on Earth.** Scan to try it, read the paper, or see the code.',
  },
];

export const mmss = (s: number): string => {
  const a = Math.abs(Math.round(s));
  return `${s < 0 ? '-' : ''}${Math.floor(a / 60)}:${String(a % 60).padStart(2, '0')}`;
};
