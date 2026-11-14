// RFC 9457 problem details, the body of every API error (PRD 8.5). Each problem has type, title,
// status, detail and instance. `type` is about:blank: these problems mean no more than their HTTP
// status, so `title` is the status phrase (RFC 9457 section 4.2.1). `instance` is the request path,
// still percent-encoded, so it stays a URI reference. The detail never carries a stack trace,
// an error message from the implementation or a server path (RFC 9457 section 5).
import { z } from '@hono/zod-openapi';

export const PROBLEM_MEDIA_TYPE = 'application/problem+json';

/**
 * The status for a path or query parameter that fails its schema. It is 400 Bad Request, not 422:
 * RFC 9110 gives 422 to request content that is well-formed but cannot be processed, and the API
 * takes only GET and HEAD requests, which have no content. The fault is in the request target.
 */
export const VALIDATION_STATUS = 400;

const TITLES = {
  400: 'Bad Request',
  404: 'Not Found',
  405: 'Method Not Allowed',
  500: 'Internal Server Error',
} as const;

/** The statuses the API answers with a problem. */
export type ProblemStatus = keyof typeof TITLES;

export const ProblemSchema = z
  .object({
    type: z.string().min(1).openapi({
      description: 'The problem type. about:blank: no more than the HTTP status says.',
      example: 'about:blank',
    }),
    title: z.string().min(1).openapi({
      description: 'The HTTP status phrase.',
      example: 'Not Found',
    }),
    status: z.number().int().min(400).max(599).openapi({
      description: 'The HTTP status code of the response.',
      example: 404,
    }),
    detail: z.string().min(1).openapi({
      description: 'What went wrong with this request, in plain words.',
      example: 'No route matches GET /api/v1/nope.',
    }),
    instance: z.string().min(1).openapi({
      description: 'The request path, percent-encoded.',
      example: '/api/v1/nope',
    }),
  })
  .strict()
  .openapi('Problem', {
    description: 'An error, as RFC 9457 problem details (application/problem+json).',
  });

export type Problem = z.infer<typeof ProblemSchema>;

/**
 * A problem+json response for the request. It is built as a new Response, so no header a handler
 * set before failing (a cache lifetime, an ETag) reaches the error, and it is never cached.
 */
export function problemResponse(
  request: Request,
  status: ProblemStatus,
  detail: string,
  headers: Readonly<Record<string, string>> = {},
): Response {
  const problem: Problem = {
    type: 'about:blank',
    title: TITLES[status],
    status,
    detail,
    instance: new URL(request.url).pathname,
  };
  return new Response(JSON.stringify(problem), {
    status,
    headers: { ...headers, 'Content-Type': PROBLEM_MEDIA_TYPE, 'Cache-Control': 'no-store' },
  });
}
