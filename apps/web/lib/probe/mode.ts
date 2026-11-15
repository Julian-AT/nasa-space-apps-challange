// cop30_mode for the live probe: proxy, by decision G0-03 (docs/ralph/DECISIONS.md,
// G0-03-RESULT, evidence outputs/g0/web-evidence.json): the GLO-30 bucket sends no CORS headers,
// so the browser reads through /api/v1/dem/cop30/{tile}.
export const COP30_MODE = 'proxy' as const;
