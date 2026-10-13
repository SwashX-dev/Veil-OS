# The constant JSON::PRETTY_STATE_PROTOTYPE was removed in version
# 3.0.0 of the json Ruby gem, which is in Sid, but it's still needed
# by stable's cucumber that we're stuck with (tails#21463). See the
# following two commits for why we redefine it as follows:
# - https://github.com/ruby/json/commit/01c47a055574d86619f5687b9928a14147cba4ee
# - https://github.com/ruby/json/commit/18e5c3c67cdce502c8120a18d49ba9c9f53f0a9c
require 'json'
unless defined?(JSON::PRETTY_STATE_PROTOTYPE)
  JSON::PRETTY_STATE_PROTOTYPE = JSON::State.new(
    indent:    '  ',
    space:     ' ',
    object_nl: "\n",
    array_nl:  "\n"
  )
end
