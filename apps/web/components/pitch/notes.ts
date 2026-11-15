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
    text: 'Before anyone walks on the Moon or drives on Mars, they **practise on Earth**. Terraluna finds **the Moon and Mars on Earth**: a physics fingerprint and a learned terrain model that compare **any place on Earth** with Moon and Mars landing sites, live in the browser.',
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
    text: 'Take the same **five-kilometre square** on both worlds, here Jezero and Rosamond. Measure how much the height changes over **270, 540 and 1080 metres**. **Compare the curves**: relief distance **0.094**, closer than **91%** of windows.',
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
    to: 66,
    text: "On top we trained **our own model**. *(Right arrow per step.)* The **input** is a 64 by 64 window: height, slope and a valid mask. A **ResNet backbone** with four stages. A **256-number embedding** used for similarity. A **VICReg head**: two augmented views must agree. A **physics head** predicts the fingerprint. And a **body adversary** with gradient reversal makes it ignore the planet.",
  },
  {
    title: 'Model metrics',
    from: 66,
    to: 75,
    text: '**2.8 million parameters**, trained on **19,000 windows** from the Moon, Mars and Earth. It trains **34 times faster** on a laptop GPU than on our server, and it runs **in the browser** with **0.99999** agreement to the training model.',
  },
  {
    title: 'The Lab',
    from: 75,
    to: 96,
    text: 'Then the **Lab**: **real elevation** for both places, side by side **at the same scale**. A simulated **rover drives the same route** on **Holuhraun in Iceland** and in **Jezero crater**, so you see the slopes it would face on each.',
  },
  {
    title: 'Results',
    from: 96,
    to: 114,
    text: 'The result is **seven evidence cards**, each with figures and a proposed field test. Besides Rosamond for **Jezero**, **Arcadia Planitia** matches **Holuhraun in Iceland**, closer than **97%** of all candidate windows.',
  },
  {
    title: 'Close',
    from: 114,
    to: 120,
    text: 'Terraluna: **measure any point on Earth, live.** terraluna-lovat.vercel.app',
  },
];

export const mmss = (s: number): string => {
  const a = Math.abs(Math.round(s));
  return `${s < 0 ? '-' : ''}${Math.floor(a / 60)}:${String(a % 60).padStart(2, '0')}`;
};
