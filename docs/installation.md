# Installation

Duet works with any CLI that supports Agent Skills and persistent `/goal` loops.
**Both agents must start with `/goal`.** Ordinary chat turns are unsupported: they
can exit before peer replies arrive. Installing Duet does not add the goal loop.

The bundled utility requires Python 3.11+ and shared local POSIX storage supporting
file locks, hard links, and atomic replacement. Both independently started peers
must be able to access the same canonical project path and session files.

## Install with the skills CLI

From the project that will use Duet:

```bash
npx skills add adamgogacz/duet --skill duet --copy
```

Select your CLIs when prompted. The third-party
[skills CLI](https://github.com/vercel-labs/skills) supports GitHub repositories,
local directories, agent selection, and copy or symlink installation. Node.js is
needed only for that installer; it is not a Duet runtime requirement. No plugin
registration is required. Use `--list` to inspect available skills without
installing, or `-g` for user-wide installation.

## Install without Node.js

Clone the source once:

```bash
git clone https://github.com/adamgogacz/duet.git "$HOME/.local/share/duet"
```

Copy the minimum bundle into your CLI's skill directory. The example below uses
`.agents/skills/duet`; substitute the location recognized by your CLI:

```bash
mkdir -p .agents/skills/duet
cp "$HOME/.local/share/duet/SKILL.md" "$HOME/.local/share/duet/LICENSE" .agents/skills/duet/
cp -R "$HOME/.local/share/duet/scripts" "$HOME/.local/share/duet/references" .agents/skills/duet/
```

Keep `SKILL.md`, `LICENSE`, `scripts/`, and `references/` together. Tests, evals,
Git metadata, and CI files are not needed to run the installed skill. Install the
same bundle for both peers; their CLI brands and skill-directory locations may
differ. Read the source before executing its bundled scripts.

## Develop from a checkout

A symlink makes source edits available immediately. Create the appropriate parent
directory, then link to the checkout:

```bash
mkdir -p .agents/skills
ln -s /absolute/path/to/duet .agents/skills/duet
```

Again, substitute your CLI's actual skill directory. Inspect an existing
destination before creating or replacing a link. The CLI must be able to load
`SKILL.md` and run the bundled utility through the installed path:

```bash
python3 -B /path/to/installed/duet/scripts/duet.py --help
```

The `-B` option avoids writing Python bytecode caches. Reload the CLI if a newly
installed skill does not appear.

## Update an installation

After updating the checkout, repeat the copy steps for each installed copy or
rerun the skills CLI with `--copy`. Copies do not track development edits
immediately. Review local modifications before replacing an installation.
Symlink installations follow edits in their target checkout.

## Shared execution requirements

Both peers need access to the workspace coordination files and the same temporary
session board. A separate container, remote machine, or differing mount layout
needs a shared-filesystem arrangement; installing the skill alone does not
establish transport. The utility uses POSIX filesystem operations, so native
Windows requires a compatible Linux environment such as WSL.
