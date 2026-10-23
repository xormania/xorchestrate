Feature: a work order is walked end to end, and every refusal is a recorded answer

  Scenario: an opened work order is visible with its stage
    Given an initialised workflow database
    When an agent opens a work order with a title and gate kind
    Then status lists the work order at its first stage with exit code 0

  Scenario: a second claimant is refused
    Given a work order claimed by one agent
    When a different agent tries to claim it
    Then the claim exits with the dedicated claimed code and names the holder
    And the first agent still holds the claim

  Scenario: a red that passes is refused as dishonest
    Given a claimed work order at the red stage
    When the agent records red with a command that exits 0
    Then the red is refused and no red is recorded

  Scenario: green before red is refused
    Given a claimed work order that has never recorded a red
    When the agent asks for green
    Then the request is refused and the stage does not advance

  Scenario: green runs the frozen command, not a fresh one
    Given a work order whose red sealed a frozen test command
    When the agent records green with a different command
    Then the green is refused because the frozen set is the contract

  Scenario: the chain verifies after a full walk
    Given a work order walked open, claim, red, green, advance, outcome
    When verify recomputes every hash and link
    Then verify exits 0 with nothing to report

  Scenario: a claim on a work order that does not exist is refused
    Given an initialised workflow database with no work orders
    When an agent tries to claim a work order id that was never opened
    Then the claim is refused and the database is unchanged
