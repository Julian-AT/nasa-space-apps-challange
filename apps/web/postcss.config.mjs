// Tailwind CSS v4 through PostCSS, as its Next.js guide sets it up (PRD 8.2). Tailwind v4 handles
// imports and vendor prefixes itself and needs no tailwind.config file.
const config = {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};

export default config;
