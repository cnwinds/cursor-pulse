# README assets

Marketing visuals for the root README (English) and README_CN (中文).

| File | Purpose |
|------|---------|
| `demo.gif` | ~9s walkthrough of the real admin UI: quota board → account registry → key loans |
| `quota-board.png` | Quota dashboard (real UI) |
| `accounts-bind-key.png` | Account registry & key binding (real UI) |
| `key-loan.png` | Key lending records (real UI) |

These are **real screenshots** from a live deployment, anonymized before capture
(demo emails / member names / admin handle). Re-capture from a healthy demo
instance when the UI changes — never publish raw screenshots containing real
account emails or member names.

To re-build `demo.gif` after updating the PNGs (managed-Python, no ffmpeg needed):

```bash
python .dev/rebuild-readme-assets.py   # regenerate PNGs into this dir + demo.gif with crossfades
```
