---
name: terms
description: Unpack the process vocabulary in the previous response — what each term means, where it is written down, and which ones were invented on the spot and have no home yet. Use when a reply leans on words whose authority you want to check.
---

Take the assistant's **immediately preceding response**. List the
**process vocabulary** it used and say, for each word, where it came
from.

Not technical vocabulary. `SARIF`, `regex`, `subprocess`, `stdout` are
not the subject — a reader can look those up and nothing here owns
them. The subject is the words that carry **meaning specific to how
this project works**: roles, artefacts, stages, rules, and the names
for kinds of failure.

Examples of the kind of word in scope: Conductor, job, task,
runner, envelope, adapter, harness, work order, gate, stage, receipt,
frozen set, chain, claim, marker, red, green, snapshot, planted fault,
honest red, silent pass, crossing, refusal, lane, vocabulary wall.

## The question to answer per term

**Where is this written down, and by whom?** Find out; do not answer
from memory. Search in this order and stop at the first hit:

```sh
git grep -n '<term>' -- AGENTS.md CONTRIB.md README.md
git grep -n '<term>' -- ops/process ops/devlane/*/CONTRACT.md ops/devlane/workflow/PLAN.md
git grep -n '<term>' -- ops/docs
git grep -n '<term>' -- ops/devlane
```

Then classify it into exactly one origin:

| origin | means |
|:--|:--|
| **repo** | written down in this repository — cite `path:line` |
| **owner** | the owner introduced it in conversation; not yet written down |
| **coined** | the assistant made it up, here, in this conversation |
| **imported** | it comes from the assistant's own global doctrine (`~/.claude/CLAUDE.md`) rather than from this project |

The last two are the point of the skill. **Coined** and **imported**
terms carry no authority in this repo — nobody agreed to them, no
document defines them, and the other harnesses do not share them
except by accident. Say so plainly rather than presenting them as
established.

`imported` deserves its own flag because it is easy to miss: vocabulary
from a personal doctrine file reads exactly like project vocabulary,
and at least one other harness loads that file, which spreads it
further without anyone deciding to adopt it.

## Also flag

- **Homonyms.** A word meaning one thing in the dev lane and another
  in the House product, or one thing here and another in the wider
  industry. These do real damage — `recipe` had to be renamed for
  exactly this reason. Check the vocabulary wall's reserved list:
  `sed -n '/^RESERVED = \[/,/^\]/p' ops/devlane/workflow/checks/vocabulary_wall.py`
- **Drift.** A repo term being used in the response with a meaning the
  cited source does not support. Quote the source and the usage.
- **Synonyms.** Two words in the response for one thing, or one word
  for two things.

## Colour, which is the part the owner reads

Every term carries a marker. The marker encodes **how much it costs to
leave undecided**, not whether it is a good word:

| marker | meaning | test |
|:--|:--|:--|
| green circle | settled | established in the repo by hands other than the assistant's, or the owner ratified it |
| yellow circle | proposed | the assistant introduced it and it lives in conversation only — cheap to change |
| red circle | decide now | unratified AND already in the tree: a contract, a module name, an argument name, or another harness's input |

Red is not "bad". Red is "this is getting expensive" — a word that has
already been committed without being agreed to, where the cost of a
rename grows with every file that adopts it.

The trap this exists to catch: a term the assistant coined THIS SESSION
and then committed reads as established, because `git grep` finds it.
So when deciding green, check WHO wrote the files it appears in. If the
only hands are the assistant's, and recently, it is red or yellow — not
green.

```sh
git log --format='%an %ad' --date=short -1 -- <file>
git log -S '<term>' --format='%h %ad %s' --date=short -- . | tail -5
```

## Output

Terms first, marked, most expensive to leave undecided first — not
alphabetical:

| | term | origin | means | written down at |

Then, only if there is anything to say:

**Decide** — every `coined` and `owner` term, with the choice each one
needs: adopt it into a named document, rename it, or drop it. A term in
active use with no home is the state this skill exists to surface.

Keep each definition to a sentence or two. Do not re-explain the
previous response; define its words.
