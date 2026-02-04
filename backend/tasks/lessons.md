# HPD Leads — Lessons Learned

Record mistakes and corrections here so future agents don't repeat them.

## Format

```
### [Date] Issue Title
**Mistake:** What went wrong
**Correction:** What the fix was
**Prevention:** Rule to follow in future
```

---

## Lessons

*None yet — this file will be updated as we encounter issues.*

---

## Patterns to Follow

1. **Always read docs/ before coding a module** — Context is already there
2. **Test with small data first** — Use `$limit=100` on API calls during dev
3. **Cache API responses** — Don't re-fetch the same data repeatedly
4. **Check rate limits** — Especially for enrichment APIs
5. **Preserve manual columns** — Never overwrite user-edited sheet columns
