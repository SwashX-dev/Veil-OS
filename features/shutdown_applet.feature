@product
Feature: Shutdown buttons in the system menu

  Scenario: I can shutdown Tails via the system menu
    Given I have started Tails from DVD and logged in and the network is connected
    When I open the system menu
    And I request a shutdown using the system menu
    Then Tails eventually shuts down

  @not_release_blocker
  Scenario: I can reboot Tails via the system menu
    Given I have started Tails from DVD and logged in and the network is connected
    When I open the system menu
    When I request a reboot using the system menu
    Then Tails eventually restarts

  Scenario: The logout button is not visible
    Given I have started Tails from DVD and logged in and the network is connected
    When I open the system menu
    Then the logout button is not visible
