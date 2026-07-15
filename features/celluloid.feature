@product
Feature: Using Celluloid
  As a Tails user
  I want to watch local videos in Celluloid
  And Flatpak portals should make it possible to open files in any location despite Flatpak's confinement

  Background:
    Given I create sample videos

  Scenario: Watching a MP4 video stored on the non-persistent filesystem
    Given I have started Tails from DVD without network and logged in
    And I plug and mount a USB drive containing sample videos
    And I copy the sample videos to "/home/amnesia" as user "amnesia"
    And the file "/home/amnesia/video.mp4" exists
    When I start "Files" via GNOME Activities Overview
    And I open "/home/amnesia/video.mp4" in Files
    Then I see the 30s-long video loaded after at most 10 seconds

  # The next scenario is similar to the previous one, except it opens the file in another way.
  # Since we're dealing with desktop portals, it's not entirely obvious that
  # clicking on "Open" from within Celluloid follows the same code path as
  # double-clicking on a file in Files. Now we have two tests follow those
  # two paths.
  Scenario: Watching MP4 videos stored in the Persistent Storage
    Given I have started Tails without network from a USB drive with a persistent partition enabled and logged in
    And I plug and mount a USB drive containing sample videos
    And I copy the sample videos to "/home/amnesia/Persistent" as user "amnesia"
    When I start "Celluloid" via GNOME Activities Overview
    And I open "/home/amnesia/Persistent/video.mp4" in Celluloid
    Then I see the 30s-long video loaded after at most 10 seconds
