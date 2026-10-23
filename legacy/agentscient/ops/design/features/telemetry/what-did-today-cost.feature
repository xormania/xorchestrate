Feature: "what did today cost" is answered by commands over the stores, never by memory

  Scenario: the report joins spend and results for a window
    Given claude, codex, and grok stores with sessions inside the window
    And a repository whose first-parent history landed a numbered merge
    When worth report runs with an injected --now
    Then each harness line carries its window totals
    And the results name the merged PR number
    And the stamp names the head, branch, and window

  Scenario: an absent store is unrecorded, never zero
    Given no store directory for a harness
    When worth report runs
    Then that harness line says unrecorded and carries no zero token figures

  Scenario: a grok session with unreadable spend holds its denominator place
    Given one grok session with usage and one whose updates file is unreadable
    When worth report runs
    Then the grok line says counted=1/2

  Scenario: waste ranks the window's sessions and names the heavy turn
    Given sessions of different window spend across harnesses
    When worth waste runs with a top limit
    Then sessions rank by window total with ties broken by id
    And the heaviest single message is named with its session

  Scenario: a malformed store timestamp is a gap, not a crash
    Given a session row whose timestamp is not a timestamp
    When worth report runs
    Then the command exits 0 and that session's spend is unrecorded

  Scenario: pre-window spend stays out of the window
    Given a codex session straddling the window start
    When worth report runs for the window
    Then only the spend after the last pre-window counter is charged

  Scenario: a dispatched Claude run's USD appears in the report
    Given a dispatch record for a Claude run whose session.spend carries cost_usd
    When worth report runs over the dispatch store
    Then the dispatches block names that record id and job
    And the Claude dispatch line carries its tokens and cost_usd

  Scenario: a Codex dispatch prints tokens and cost=unrecorded
    Given a dispatch record for a Codex run with tokens and no dollar wire
    When worth report runs over the dispatch store
    Then the Codex dispatch line carries its tokens and cost=unrecorded
    And it does not print cost 0

  Scenario: a Grok dispatch prints tokens and ticks
    Given a dispatch record for a Grok run whose session.spend carries cost_usd_ticks
    When worth report runs over the dispatch store
    Then the Grok dispatch line carries its tokens and cost_usd_ticks

  Scenario: a job directory without a record is counted and unrecorded
    Given a job directory in the dispatch store with no record.json
    When worth report runs over the dispatch store
    Then that id holds a place in counted=N/M
    And its spend prints unrecorded, never 0

  Scenario: a dispatch launched before the window is not charged to it
    Given one dispatch launched inside the window and one launched before it
    When worth report runs for the window
    Then only the in-window dispatch is charged

  Scenario: a dispatched stream is not also counted as a session
    Given a Claude stream whose cwd is the dispatch snapshot and a dispatch record for that run
    When worth report and waste run
    Then the spend is counted once, as a dispatch row, not as a session

  Scenario: --claude-dir has no argparse default
    Given a lane conf that does not declare CLAUDE_DIR
    When worth waste runs without --claude-dir
    Then the command exits 2 with class conf-key naming CLAUDE_DIR

  Scenario: CLAUDE_DIR from lane conf is resolved relative to HOME
    Given CLAUDE_DIR='from-conf' in the lane conf
    And a Claude session planted under $HOME/from-conf
    When worth waste runs without --claude-dir
    Then that session is ranked

  Scenario: a waste Claude session row carries turns, avg_context, and refusals
    Given a Claude transcript with two assistant usage rows and one tool-result deny
    When worth waste runs
    Then the session row carries turns, avg_context, and refusals from those definitions
    And JSON and plain carry the same figures

  Scenario: an unreadable transcript prints unrecorded for turns, avg_context, and refusals
    Given a Claude session file that cannot be parsed
    When worth waste runs
    Then those three fields print unrecorded and never 0
