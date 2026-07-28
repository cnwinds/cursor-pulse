# README assets

Marketing visuals for the root README and README_EN.

| File | Purpose |
|------|---------|
| `demo.gif` | ~20s walkthrough: login → quota board → accounts → key loan |
| `login.png` | Login frame (used in GIF) |
| `quota-board.png` | Quota dashboard |
| `accounts-bind-key.png` | Account registry & key binding |
| `key-loan.png` | Key lending |

These are **UI previews** for documentation. Replace with screen recordings / screenshots from a real deployment when available.

To re-build `demo.gif` after updating PNGs:

```bash
cd docs/assets/readme
ffmpeg -y -hide_banner -loglevel error \
  -loop 1 -t 5 -i login.png \
  -loop 1 -t 5 -i quota-board.png \
  -loop 1 -t 5 -i accounts-bind-key.png \
  -loop 1 -t 5 -i key-loan.png \
  -filter_complex "[0:v]scale=960:-2:flags=lanczos[v0];[1:v]scale=960:-2:flags=lanczos[v1];[2:v]scale=960:-2:flags=lanczos[v2];[3:v]scale=960:-2:flags=lanczos[v3];[v0][v1]xfade=transition=fade:duration=0.5:offset=4.5[v01];[v01][v2]xfade=transition=fade:duration=0.5:offset=9.0[v012];[v012][v3]xfade=transition=fade:duration=0.5:offset=13.5,format=rgb24,fps=8,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3" \
  demo.gif
```
