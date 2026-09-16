@product
Feature: Installing and running Flatpak apps in Tails
  As a Tails user
  I may want to install and run software not shipped in Tails or Debian
  And keep them up-to-date

  Scenario: GNOME Software does not have the Debian plugin
    Given I have the build manifest for the image under test
    Then Debian package gnome-software-plugin-deb is not installed

  @check_tor_leaks
  Scenario: GNOME Software does not start until Tor is ready
    Given I have started Tails without network with the Flatpak feature from a USB drive with a persistent partition enabled and logged in
    Then process "gnome-software" is not running
    When I try to start GNOME Software
    Then I see an error about GNOME Software requiring Tor to be ready
    And I close the "zenity" window
    And process "gnome-software" is not running
    When the network is plugged
    And Tor is ready
    Then process "gnome-software" is running within 10 seconds

  @check_tor_leaks
  Scenario: Installing, starting and uninstalling a Flatpak
    Given I have started Tails with the Flatpak feature from a USB drive with a persistent partition enabled and logged in and the network is connected
    When I start GNOME Software
    And I install Signal Desktop using GNOME Software
    Then the org.signal.Signal Flatpak is installed after at most 600 seconds
    When I start the org.signal.Signal Flatpak
    Then the org.signal.Signal Flatpak is running
    Given I kill the org.signal.Signal Flatpak
    And I go to the main screen of GNOME Software
    When I uninstall Signal Desktop using GNOME Software
    Then the org.signal.Signal Flatpak is uninstalled after at most 60 seconds

  @check_tor_leaks
  Scenario: Changing locale after installing a Flatpak
    Given I have started Tails with the Flatpak feature from a USB drive with a persistent partition enabled and logged in and the network is connected
    When I start GNOME Software
    And I install Thunderbird using GNOME Software
    Then the org.mozilla.thunderbird Flatpak is installed after at most 600 seconds
    And the "de" locale is not present in the org.mozilla.thunderbird.Locale Flatpak extension
    Given I set Tails to boot with options "flatpak"
    When I cold reboot the computer
    Then the computer boots Tails
    When I enable persistence
    And I log in to a new session in German (de)
    Given it has been 3 days since GNOME Software checked for updates
    And GNOME Software thinks it is after 06:00 but before 23:00
    When the network is plugged
    And Tor is ready
    Then the "de" locale is present in the org.mozilla.thunderbird.Locale Flatpak extension after at most 600 seconds
