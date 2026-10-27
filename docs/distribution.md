# Distribution choices

Duet ships as one ordinary Agent Skill at the repository root. Both participating
clients must already provide persistent `/goal` loops; the distribution does not
install or emulate that required host behavior. The source bundle
is the installable unit; users can install it with an existing skills installer or
copy its instructions, scripts, references, and license. There is no package-index
release, build step, plugin manifest, marketplace registration, or runtime service.

## What comparable projects do

| Project / contract | Observed approach | Decision for Duet |
| --- | --- | --- |
| [Agent Skills specification](https://agentskills.io/specification) | A named directory with SKILL.md and optional bundled resources. | Keep a self-contained portable bundle. |
| [Vercel agent-skills](https://github.com/vercel-labs/agent-skills) | GitHub-hosted skill directories, advertised through the skills ecosystem. | Offer a GitHub installation command rather than a new installer. |
| [Vercel skills CLI](https://github.com/vercel-labs/skills/blob/main/docs/commands.md) | Installs from Git/local sources into selected clients; supports copies and symlinks. | Document project-local copies first and symlinks for development. |
| [Anthropic skills](https://github.com/anthropics/skills) | Self-contained skill folders; its README also documents Claude plugin distribution. | Preserve the folder contract; a plugin wrapper adds no current Duet capability. |
| [Codex skill documentation](https://developers.openai.com/codex/skills) | Documents direct local folders and symlinks; recommends plugins for broader distribution. | Use the supported folder loader, with a cross-client installer as an optional distribution route. |

The [skills CLI discovery implementation](https://github.com/vercel-labs/skills/blob/main/src/skills.ts)
explicitly recognizes a root SKILL.md. A single-skill repo therefore does not need
a duplicate `skills/duet` tree. The repository directory name and skill name are
both `duet`.

OpenAI's older [skills catalog](https://github.com/openai/skills) is now marked
deprecated in favor of plugins. It is useful historical context, not the source of
current Codex installation instructions. Direct folder loading remains documented.
Duet's choice is a packaging preference, not a claim that the vendors recommend
identical distribution mechanisms.

## Copies versus development links

A copy is self-contained and updates explicitly. A symlink follows source edits
immediately and avoids divergent developer copies. Choose the method that fits
your workflow and install it in the skill directory recognized by your CLI. Both
methods use the same portable instructions and Python utility.

See [installation](installation.md) for concrete commands. Runtime behavior is
independent of the chosen installation mechanism.

## Publication and validation

The Apache-2.0 license applies to this repository. Installing the runtime bundle
must retain LICENSE. GitHub source downloads can be used directly; no generated
release artifact is required. Publishing remains a normal reviewed Git commit/push.

The test suite exercises an installed minimum bundle from a temporary directory
outside the checkout. CI is configured to cover the minimum Python version and another supported
version on Linux, plus macOS. Live model-driven validation remains distinct from
utility and packaging tests; the eval scenarios describe what to check.
