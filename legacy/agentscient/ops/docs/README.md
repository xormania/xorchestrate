# The shelf — lessons the process law cites

Four pages, carried from the House lane at `99334f7` through
`lane/workflow` (PR #10, closed unmerged; I-008). They are records of
measured incidents, not law: `AGENTS.md` and `docs/` rule, and where a
page disagrees with them the page is the older reading. Each opens
with frontmatter (`name`, `description`, `covers:` naming the paths it
describes); a `covers:` entry names only a path present in this tree.

| page | what it holds |
|:--|:--|
| `cue-sys.md` | what CUE turned out to be for in agent development, each lesson with the incident behind it |
| `cue-aar-equality.md` | four CUE constructs that read as constraints and enforced nothing, measured |
| `mini-app-lessons.md` | what building the dev-lane mini apps taught, kept so the twins are written better |
| `rust-agent-tooling.md` | the verified landscape of Rust CI and diagnostic tools, the input to a Rust target's gate profile |

`ops/process/pipeline.md` §The evidence behind all of this names the
first three; read them before writing a contract. The House's design
memos about this framework are under `ops/archive/house/`, not here.

To add a page: write it with the frontmatter, add its row here. No
index test holds this table yet; that absence is declared.
