// The API instance that Next serves (PRD 8.5, D24): app/api/[[...route]]/route.ts hands every
// request to it. It uses createApi's defaults: the T15-01 loader over apps/web/data/ (under
// process.cwd(), where bundle:pull writes) and process.env, both read on each request, and
// console.error for errors that become a 500 or 503. Route tests import this same instance.
import { createApi } from './app.ts';

export const app = createApi();
