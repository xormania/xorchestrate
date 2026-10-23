Feature: a runaway background reviewer is tripped, and the trip leaves evidence

  Scenario: the token wall trips a reviewer that spent past its cap
    Given a reviewer stream whose usage exceeds the armed --cap
    When the battery drains the stream
    Then the battery exits 3
    And the TRIPPED file names the wall that fired

  Scenario: the output-token wall trips independently of the total wall
    Given a stream whose output tokens exceed --cap-out while totals stay under --cap
    When the battery drains the stream
    Then the battery exits 3 for the output wall alone

  Scenario: a zero cap is a disarmed wall
    Given a stream that would trip a nonzero cap
    When the battery runs with that cap at its zero default
    Then the battery exits 0 because an unarmed wall cannot fire

  Scenario: grok usage feeds the walls across a run reset
    Given a grok stream whose reported totals shrink between turns
    When the battery drains the stream
    Then banked spend from before the shrink still counts toward the cap

  Scenario: a malformed record neither kills the battery nor counts
    Given a stream carrying a JSON-RPC batch line and non-numeric counters
    When the battery drains the stream
    Then the battery neither crashes nor charges the malformed records
    And the walls still answer for the numeric records alone

  Scenario: a dead reviewer ends supervision without a trip
    Given an armed battery whose supervised process has exited
    When the battery notices the process is gone
    Then the battery ends without exit code 3
