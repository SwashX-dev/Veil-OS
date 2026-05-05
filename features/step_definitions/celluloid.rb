require 'resolv'
require 'uri'

Given /^I create sample videos$/ do
  @video_dir_on_host = "#{$config['TMPDIR']}/video_dir"
  FileUtils.mkdir_p(@video_dir_on_host)
  add_after_scenario_hook { FileUtils.rm_r(@video_dir_on_host) }
  fatal_system('ffmpeg -loop 1 -t 30 -f image2 ' \
               "-i 'features/images/USBTailsLogo.png' " \
               '-an -vcodec libx264 -y ' \
               '-filter:v "crop=in_w-mod(in_w\,2):in_h-mod(in_h\,2)" ' \
               "'#{@video_dir_on_host}/video.mp4' >/dev/null 2>&1")
end

Given /^I plug and mount a USB drive containing sample videos$/ do
  @video_dir_on_guest = share_host_files(
    Dir.glob("#{@video_dir_on_host}/*")
  )
end

Given /^I copy the sample videos to "([^"]+)" as user "([^"]+)"$/ do |destination, user|
  Dir.glob("#{@video_dir_on_host}/*.mp4").each do |video_on_host|
    video_name = File.basename(video_on_host)
    src_on_guest = "#{@video_dir_on_guest}/#{video_name}"
    dst_on_guest = "#{destination}/#{video_name}"
    step "I copy \"#{src_on_guest}\" to \"#{dst_on_guest}\" as user \"#{user}\""
  end
end

When(/^I open "(.*[.].*)" in Celluloid$/) do |filepath|
  dir = File.dirname(filepath)
  filename = File.basename(filepath)
  nautilus = Dogtail::Application.new('org.gnome.Nautilus')
  if dir != '.' && dir != '/home/amnesia'
    @screen.press('ctrl', 'l')
    @screen.type(dir)
    @screen.press('Return')
  end
  dialog = nautilus.child('Open File', roleName: 'frame')
  dialog.child(filename, roleName: 'table cell').click
  @screen.press('Return')
end

def celluloid
  Dogtail::Application.new('io.github.celluloid_player.Celluloid')
end

Then(/^I see the (\d+)s-long video loaded after at most (\d+) seconds$/) do |duration, timeout|
  window = celluloid.child(roleName: 'frame', recursive: false)
  # Maximize the window, otherwise the duration will be hidden
  window.doActionNamed('window.toggle-maximized')

  progress_node = nil
  try_for(timeout.to_i) do
    progress_re = %r{\d+:\d+/\d+:\d+}
    progress_node = window.children(roleName: 'label', recursive: true).select do |n|
      n.name && progress_re.match(n.name)
    end
    progress_node = progress_node.first
    !progress_node.nil?
  end
  progress = progress_node.name
  reported_duration_human = progress.split('/').last.split(':').map(&:to_i)
  reported_duration_human[0] *= 60
  reported_duration_seconds = reported_duration_human.sum
  assert_equal(reported_duration_seconds, duration.to_i)
end
