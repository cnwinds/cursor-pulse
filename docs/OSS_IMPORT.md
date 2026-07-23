# Open-source: clean repository import

This tree is prepared for a **new empty GitHub repository** (squash import), so old commits that contained real usage CSV / PII never ship with the public history.

## Maintainer steps

1. Finish Phase A scrub on this working tree; ensure `pytest` is green.
2. On GitHub: create a **new empty** repo (e.g. `cnwinds/cursor-pulse` after renaming the old one to `cursor-pulse-private`, or use a new name).
3. Locally produce a single-commit orphan branch:

```bash
# from a clean working tree with all Phase A changes committed on master
git checkout --orphan oss-main
git add -A
git status   # confirm no .env, data/, *.pem
git commit -m "chore: initial public import under MIT"
```

4. Push to the new remote only:

```bash
git remote rename origin origin-legacy   # keep old remote for private history
git remote add origin https://github.com/<org>/<new-repo>.git
git push -u origin oss-main:main
```

5. Rotate all secrets that were ever used with the old public/private clone (DingTalk, JWT, encryption keys, Cursor keys).
6. Archive or make private the legacy repository; do not force-push rewrite unless you accept breaking all forks.

## Do not include in the import

- `.env`, `config.yaml`, `data/`, `.dev/`, `*.pem`, `*.key`, `*.db`
- Personal emails, real chatIds, machine-absolute paths (already scrubbed in tree)

## License

MIT — see `LICENSE`.
