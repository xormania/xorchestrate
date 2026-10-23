Feature: a multi-worktree day leaves a stream every actor can read

  Scenario: a branch checkout is recorded with its actor and worktree
    Given a clone with the git-native hooks installed and WF_AGENT set
    When an actor checks out a branch in a worktree
    Then the shared context stream gains a line naming the actor, the branch, and the worktree

  Scenario: a file checkout is not a crossing
    Given the hooks installed
    When an actor checks out a single file
    Then the context stream does not change

  Scenario: a commit is recorded from whichever worktree made it
    Given two worktrees of one clone
    When each worktree commits
    Then the shared stream records both commits with their worktree names

  Scenario: an unset WF_AGENT falls back to the system user
    Given the hooks installed and WF_AGENT unset
    When an actor checks out a branch
    Then the stream line names the system user instead of an agent

  Scenario: a hook failure never breaks git
    Given a context stream that cannot be written
    When an actor checks out a branch
    Then the checkout succeeds anyway and only the record is lost

  Scenario: control characters cannot corrupt the stream
    Given a branch name carrying newline-adjacent characters
    When the checkout is recorded
    Then the stream line is one valid JSON object
