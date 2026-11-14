// Conventional Commits, checked by the husky commit-msg hook and by scripts/ralph/ralph.sh.
// Scopes are lower-case: a story id (t0-01), a package or area (schema, web, pipeline), or ralph.
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // Bodies and trailers may carry long paths, command output and Co-Authored-By lines.
    'body-max-line-length': [0],
    'footer-max-line-length': [0],
  },
};
