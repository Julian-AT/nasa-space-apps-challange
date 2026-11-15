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
    text: 'Before anyone walks on the Moon or drives on Mars, they **practise on Earth**. Terraluna finds **the Moon and Mars on Earth**, live in the browser.',
  },
  {
    title: 'Problem',
    from: 8,
    to: 20,
    text: "Astronauts and rovers train on Earth first. But which place on Earth **really matches the target**? We have **three target sites**: Connecting Ridge on the Moon, Jezero and Arcadia on Mars. Today, analog sites are chosen **by eye**, by experience, not by one shared terrain measure. Terraluna measures **any point on Earth**, live, in seconds.",
  },
  {
    title: 'Physics fingerprint',
    from: 20,
    to: 34,
    text: 'We compute a **physics fingerprint** of relief and texture for every five-kilometre window, **71 Earth windows per target**. Every dot is one window. For **Jezero**, the best Earth match is **15 times closer** than the median window.',
  },
  {
    title: 'Live finder',
    from: 34,
    to: 48,
    text: "Here's the **finder**: click any spot, and we fetch **real elevation** and compute the fingerprint **right here in the browser**. *(Press the right arrow.)* The globe flies to **Rosamond Dry Lake**, and your browser measures it **live** against **Jezero crater**, in about **two seconds**.",
  },
  {
    title: 'Our model: architecture',
    from: 48,
    to: 62,
    text: "On top we trained **our own model**, self-supervised. *(Right arrow per step.)* The **input** is one 5 km window: height, slope and a mask. We make **two augmented views** of it. **One shared encoder** turns both into **256-number embeddings**. **VICReg** makes the views agree and keeps features spread out. A **physics head** predicts the fingerprint. A **body adversary** makes it learn terrain, **not which planet**. And here is the **full architecture**.",
  },
  {
    title: 'Training',
    from: 62,
    to: 70,
    text: 'Training on Moon, Mars and Earth windows: the **self-supervised loss falls**, and the **physics error drops tenfold**, so the encoder learns the fingerprint.',
  },
  {
    title: 'Benchmarks',
    from: 70,
    to: 80,
    text: '**2.8 million parameters**, **19,000 training windows**. Training runs **34 times faster** on a laptop GPU than on our CPU server. In the browser the model agrees with training at **0.99999** cosine. And the best Earth match for **Jezero** sits at relief distance **0.094**.',
  },
  {
    title: 'The Lab',
    from: 80,
    to: 96,
    text: 'Then the **Lab**: **real elevation** for both places, side by side **at the same scale**. A simulated **rover drives the same route** on **Holuhraun in Iceland** and in **Jezero crater**, so you see the slopes it would face on each.',
  },
  {
    title: 'Results',
    from: 96,
    to: 114,
    text: 'The result is **seven evidence cards**. One row per target: the best Earth match and how many times closer it is than the median window. **Jezero** and **Arcadia** both find **Holuhraun in Iceland**, and every card comes with figures and a proposed field test.',
  },
  {
    title: 'Close',
    from: 114,
    to: 120,
    text: 'Terraluna: **measure any point on Earth, live.** nasa.julianschmidt.cv',
  },
];

export const mmss = (s: number): string => {
  const a = Math.abs(Math.round(s));
  return `${s < 0 ? '-' : ''}${Math.floor(a / 60)}:${String(a % 60).padStart(2, '0')}`;
};
