## Summary

<!-- What does this PR change and why? -->

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Documentation
- [ ] Refactor (no behavior change)
- [ ] Tests

## Risk area

- [ ] Strategy / order execution
- [ ] Risk / emergency
- [ ] Credentials / env routing
- [ ] Frontend only
- [ ] None of the above

## Checklist

- [ ] CI **Backend tests (pytest)** is green (or locally: `python -m pytest backend/tests -q`)
- [ ] No API keys or `.env` in the diff
- [ ] Contract fields unchanged (`generatorUpper`, `allocatedCapital`, …)
- [ ] `.env.example` updated if new env vars
- [ ] Docs updated if behavior changed
