def thunderbird_app
  Dogtail::Application.new('Thunderbird')
end

def thunderbird_main
  thunderbird_app.child(roleName: 'frame', recursive: false)
end

def thunderbird_wizard
  # Unfortunately the wizard has no title set and its contents (that
  # is visible to Dogtail, which doesn't include the "Account Hub"
  # header for some reason) change completely between steps, except
  # for some generic links in the footer, e.g. "Privacy Policy".
  thunderbird_main.children(roleName: 'dialog', recursive: false)
                  .find do |dialog|
    dialog.child('Privacy Policy', roleName: 'list item')
  end
end

def thunderbird_inbox
  folder_view = thunderbird_main.child($config['Thunderbird']['address'],
                                       roleName: 'tree item')
  # Dogtail mangles the regexps we pass it, which we have to workaround,
  # hence the suboptimal regexp below (the simpler /^Inbox( .*)?$/ would
  # not work). For details, see
  # https://gitlab.tails.net/tails/tails/-/issues/19928#note_215864
  folder_view.child(/^Inbox|Inbox (.*)$/, roleName: 'tree item')
end

When /^I start Thunderbird$/ do
  workaround_pref_lines = [
    # When we generate a random subject line it may contain one of the
    # keywords that will make Thunderbird show an extra prompt when trying
    # to send an email. Let's disable this feature.
    'pref("mail.compose.attachment_reminder", false);',
  ]
  workaround_pref_lines.each do |line|
    $vm.file_append('/etc/thunderbird/pref/thunderbird.js', "#{line}\n")
  end
  launch_thunderbird
end

When /^I have not configured an email account yet$/ do
  conf_path = "/home/#{LIVE_USER}/.thunderbird/profile.default/prefs.js"
  if $vm.file_exist?(conf_path)
    thunderbird_prefs = $vm.file_content(conf_path).chomp
    assert(!thunderbird_prefs.include?('mail.accountmanager.accounts'))
  end
end

Then /^I am prompted to setup an email account$/ do
  thunderbird_wizard
end

Then /^I minimize the autoconfiguration wizard$/ do
  thunderbird_wizard.button('Minimize').press
end

Then /^I open Thunderbird's Add-ons Manager$/ do
  # Make sure AppMenu is available, even if it seems hard to click its
  # "Add-ons" menu + menu item...
  thunderbird_main.button('AppMenu')
  # ... then use keyboard shortcuts, with a little delay between both
  # so that the menu has a chance to pop up:
  @screen.press('alt', 't')
  sleep(1)
  @screen.type('a')
  @thunderbird_addons = thunderbird_app.child(
    'Add-ons Manager', roleName: 'document web'
  )
end

Then /^I open the Extensions tab$/ do
  # Sometimes the Add-on manager loads its GUI slowly, so that the
  # tabs move around, creating a race where we might find the
  # Extensions tab at one place but it has moved to another when we
  # finally do the click.
  try_for(10) do
    @thunderbird_addons
      .child('Extensions', roleName: 'page tab', retry: false).press
    # Verify that we clicked correctly:
    @thunderbird_addons
      .child('Manage Your Extensions', roleName: 'heading', retry: false)
  end
end

Then /^I see that no add-ons are enabled in Thunderbird$/ do
  assert(!@thunderbird_addons.child?('Enabled', roleName: 'heading'))
end

When /^I enter my email credentials into the autoconfiguration wizard$/ do
  address = $config['Thunderbird']['address']
  name = address.split('@').first
  hostname = address.split('@').last

  # These tricks are needed because on Jenkins, the hostname of the test email
  # server resolves to a RFC 1918 address (sysadmin#18044), which tor would not allow
  # connecting to, and the firewall leak checker would make a fuss
  # out of it.
  allow_connecting_to_possibly_rfc1918_host(hostname)

  thunderbird_wizard.child('Full name*', roleName: 'entry').grabFocus
  @screen.paste(name)
  thunderbird_wizard.child('Email address*',
                           roleName: 'entry').grabFocus
  @screen.paste(address)
  thunderbird_wizard.button('Continue').press
  # This heading is shown if and only if a configuration has been found
  try_for(120) do
    thunderbird_wizard.child('Connect your email settings', roleName: 'heading')
  end
end

Then /^the autoconfiguration wizard found only secure settings$/ do
  thunderbird_wizard.children(roleName: 'paragraph')
                    .select { |n| n.text == 'SECURITY:' }
                    .each do |security_field|
    values = security_field.parent.children(roleName: 'section')
    assert_equal(1, values.size)
    assert_include(['SSL/TLS', 'STARTTLS'], values.first.text)
  end
end

When /^I fetch my email$/ do
  thunderbird_main.button('Get Messages').press
end

When /^I accept the (?:autoconfiguration wizard's|manual) configuration$/ do
  thunderbird_wizard.button('Continue').press
end

When /^I enter my password and finalize the email account setup$/ do
  thunderbird_wizard.child('Password*', roleName: 'password text').grabFocus
  @screen.paste($config['Thunderbird']['password'])
  thunderbird_wizard.button('Continue').press
  thunderbird_wizard.child('Sync your calendars and address books', roleName: 'heading')
  thunderbird_wizard.button('Skip').press
  thunderbird_wizard.button('Finish').press
end

When /^I select the autoconfiguration wizard's IMAP choice$/ do
  thunderbird_wizard.child('IMAP (remote folders)', roleName: 'radio button').select
end

When /^I send an email to myself$/ do
  thunderbird_main.button('New Message').press
  compose_window = thunderbird_app.child('Write: (no subject) - Thunderbird')
  compose_window.child('To', roleName: 'entry').grabFocus
  @screen.paste($config['Thunderbird']['address'])
  # The randomness of the subject will make it easier for us to later
  # find *exactly* this email. This makes it safe to run several tests
  # in parallel.
  @subject = "Automated test suite: #{random_alnum_string(32)}"
  compose_window.child('Subject', roleName: 'entry').grabFocus
  @screen.paste(@subject)
  compose_window = thunderbird_app.child("Write: #{@subject} - Thunderbird")
  compose_window.child('Message body', roleName: 'document web').grabFocus
  @screen.type('test')
  compose_window.child('Composition Toolbar', roleName: 'tool bar')
                .button('Send').press
  try_for(120, delay: 2) do
    !compose_window.exist?
  end
end

Then /^I can find the email I sent to myself in my inbox$/ do
  recovery_proc = proc { step 'I fetch my email' }
  retry_tor(recovery_proc) do
    thunderbird_inbox.activate
    thunderbird_main.child('Quick Filter',
                           roleName: 'toggle button')
                    .press
    thunderbird_main.child('Filter messages',
                           roleName: 'entry')
                    .grabFocus
    @screen.paste(@subject)
    address = $config['Thunderbird']['address']
    name = address.split('@').first
    message = thunderbird_main.child(
      "#{name} <#{address}>,.*, #{@subject}, Unread", roleName: 'table row'
    )
    # Let's clean up
    message.grabFocus
    @screen.press('space')
    thunderbird_main.button('Delete').press
  end
end

Then(/^the screen keyboard works in Thunderbird$/) do
  step 'I start Thunderbird'
  osk_key = 'ScreenKeyboardKeyX.png'
  thunderbird_x = 'ThunderbirdX.png'
  case $language
  when 'Arabic'
    thunderbird_x = 'ThunderbirdXRTL.png'
  when 'Persian'
    osk_key = 'ScreenKeyboardKeyPersian.png'
    thunderbird_x = 'ThunderbirdXPersian.png'
  end
  # We have to click to activate the screen keyboard (#19101),
  # but we cannot do it with Dogtail so we have to use a picture.
  @screen.wait('ThunderbirdTextEntry.png', 20).click
  @screen.wait('ScreenKeyboard.png', 20)
  @screen.wait(osk_key, 20).click
  @screen.wait(thunderbird_x, 20)
end

def thunderbird_non_suspicious_connections
  [
    # Used to get add-on lists
    'addons.thunderbird.net', 'services.addons.thunderbird.net',
    # Used for many things, in particular the account auto config
    # database (mailnews.auto_config_url pref)
    'live.thunderbird.net',
    # Used to install Thunderbird in the first place
    'cdn-fastly.deb.debian.org',
    'deb.tails.net',
    'deb.torproject.org',
    # Used by GNOME Software that is running in the background
    'dl.flathub.org',
    'odrs.gnome.org',
  ]
end

Then /^no unexpected connection has leaked from Thunderbird$/ do
  connections = exclude_non_suspicious_connections(
    tor_connections_from_log,
    expected_hosts: thunderbird_non_suspicious_connections
  )

  unless connections.empty?
    raise FirewallAssertionFailedError,
          "Unexpected connections: #{connections.join(',')}"
  end
end

Then /^the system only established connections with my email server$/ do
  all_connections = tor_connections_from_log
  assert_false(all_connections.empty?,
               'No connections have been logged; ' \
               'this suggests a problem in tor-circuits-log')
  connections = exclude_non_suspicious_connections(
    all_connections,
    expected_hosts: thunderbird_non_suspicious_connections
  )
  hosts = connections.map { |addr| addr.split(':').first }

  allowed_servers = $config['Thunderbird']['servers'] || []
  email_domain = $config['Thunderbird']['address'].split('@').last

  unwanted_connections = hosts.uniq.reject do |server|
    allowed_servers.include?(server) ||
      server == email_domain ||
      server.end_with?(".#{email_domain}")
  end

  unless unwanted_connections.empty?
    raise FirewallAssertionFailedError,
          "Unexpected connections: #{unwanted_connections.join(',')}"
  end
end

Given(/^Thunderbird is installed$/) do
  # Avoid having to deal with the Additional Software notification
  # obscuring bits of UI that we need to interact with in following
  # steps: this is a "Given" step, so we don't need to replicate
  # exactly what a user would do.
  apt_asp_conf_file = '/etc/apt/apt.conf.d/80tails-additional-software'
  $vm.execute_successfully("mv '#{apt_asp_conf_file}' '#{apt_asp_conf_file}.disabled'")

  step 'I update the APT lists using apt'
  step 'I install "thunderbird" using apt'
  # Some of our localization tests exercise specifically RTL behaviour,
  # which is only enabled when the corresponding langpack is installed.
  step 'I install "thunderbird-l10n-ar" using apt'

  # Reset the APT configuration to its original state
  $vm.execute_successfully("mv #{apt_asp_conf_file}.disabled #{apt_asp_conf_file}")
end
