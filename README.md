# multi-model

Claude Code plugin: **Claude produces, Grok 4.6 adversarially reviews, TypeSafe Jev routes and gates.**
Four pipelines — `leetcode-explain`, `general`, `paper-review`, `ue5` (Windows + Unreal Engine 5.8; `cpp` sub-mode live, see `PLAN-ue5.md`) — sharing these scripts:

- `scripts/judge.py` — TypeSafe wrapper (presets `route`, `findings`, `gate-leetcode`, `gate-general`, `gate-paper`; thresholds in code)
- `scripts/grok_review.sh` — grok CLI headless wrapper (roles `challenger`, `critic`, `reviewer`, `researcher`, `ue_reviewer`; JSON-schema output)
- `scripts/ue_env.sh` / `ue_build.sh` / `ue_test.sh` — Unreal: resolve engine from `.uproject`, build with UBT → `build.json`, run Automation tests headlessly → `test.json` (ue5 line only)

See `SKILL.md` for the contract and `EVAL.md` for measured results.

## Install (macOS / Windows)

Clone **into the Claude Code skills directory** so it auto-loads as `multi-model@skills-dir`:

```bash
# macOS / Linux
git clone git@github.com:kid2freak/multi-model.git ~/.claude/skills/multi-model
# Windows (Git Bash or PowerShell)
git clone git@github.com:kid2freak/multi-model.git "$HOME/.claude/skills/multi-model"
```

Prerequisites on every machine:

| Need | macOS | Windows |
|---|---|---|
| `TYPESAFE_API_KEY` | `export` in `~/.zshrc` (judge.py also reads it from there) | User environment variable (System Properties → Environment Variables), or `setx TYPESAFE_API_KEY "..."`; restart the terminal. judge.py also falls back to `~/.bashrc` / `~/.bash_profile` / `~/.profile` |
| grok CLI | `~/.grok/bin/grok`, logged in | install Grok Build, log in, then either put `grok` on `PATH` or set `GROK_BIN` to its full path |
| bash | built-in (3.2 is fine) | Git Bash (ships with Git for Windows); Claude Code on Windows already uses it |
| Python 3.9+ | built-in | python.org / winget; `pip install pypdf` for `pdf_text.py` |
| Unreal (ue5 line) | — | Launcher build of UE 5.8 + Visual Studio with C++ workload; the project's `EngineAssociation` must resolve via `LauncherInstalled.dat` (or set `UE_ENGINE_ROOT`) |
| Third-party bases | `git clone https://github.com/karanb192/algo-sensei ~/.claude/skills/algo-sensei` · `claude plugin marketplace add Imbad0202/academic-research-skills && claude plugin install academic-research-skills` | same commands |

Then `claude plugin validate ~/.claude/skills/multi-model` and `/reload-plugins`.

Windows notes: if `python3` is the Microsoft Store redirector stub (prints nothing, exit 49), disable it under Settings → Apps → App execution aliases or run the skills' `python3 …` commands with `python`; `grok_review.sh` detects a working interpreter itself (`PYTHON=` overrides). `grok_review.sh` writes a temp file to `/tmp` (Git Bash maps it); paths inside the skills use `~/.claude/skills/multi-model/…` which Git Bash resolves. Run artifacts go to `~/.multi-model/runs/`, outside the repo.

## Sync

```bash
cd ~/.claude/skills/multi-model && git pull   # on the other machine
```

Edit skills/scripts here, commit, push; never edit `algo-sensei` or `academic-research-skills` in place — customisations belong in this repo's wrapper skills.
