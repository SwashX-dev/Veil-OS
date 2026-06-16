require 'English'

def connection_info(packet)
  convert_port = proc(&:to_i)
  convert_dns_question = proc { |x| ["#{x}."] }
  connection = {}
  [
    [:mac_saddr, ['eth.src']],
    [:mac_daddr, ['eth.dst']],
    [:protocol, ['_ws.col.protocol']],
    [:saddr, ['ip.src', 'ipv6.src']],
    [:daddr, ['ip.dst', 'ipv6.dst']],
    [:sport, ['tcp.srcport', 'udp.srcport'], convert_port],
    [:dport, ['tcp.dstport', 'udp.dstport'], convert_port],
    [:dns_question, ['dns.qry.name'], convert_dns_question],
  ].each do |key, fields, convert_proc|
    fields.each do |field|
      next unless packet[field]

      val = packet[field].first
      val = convert_proc.call(val) if convert_proc
      connection[key] = val
      break
    end
  end
  connection
end

# Returns the unique edges (based on protocol, source/destination
# address/port) in the graph of all network flows.
def pcap_connections_helper(pcap_file,
                            ignore_arp: true,
                            ignore_dhcp: true,
                            ignore_slaac: true,
                            ignore_sources: [$vm.vmnet.bridge_mac_address])
  filters = []
  filters << '!arp' if ignore_arp
  if ignore_dhcp
    filters << '!(udp.port in {67, 68} and ' \
               'eth.dst == ff:ff:ff:ff:ff:ff and ' \
               'ip.dst == 255.255.255.255)'
  end
  # Also ignores Neighbor Discovery Protocol
  filters << '!(icmpv6.type in {133, 135, 136})' if ignore_slaac
  ignore_sources.each do |source|
    filters << "!(eth.src == #{source})"
  end
  cmd = [
    'tshark',
    '--read-file', pcap_file,
    '-T', 'json',
    '-e', 'eth.src',
    '-e', 'ip.src',
    '-e', 'ipv6.src',
    '-e', 'tcp.srcport',
    '-e', 'udp.srcport',
    '-e', 'eth.dst',
    '-e', 'ipv6.dst',
    '-e', 'ip.dst',
    '-e', 'tcp.dstport',
    '-e', 'udp.dstport',
    '-e', '_ws.col.protocol',
    '-e', 'dns.qry.name',
  ]
  cmd += ['--display-filter', filters.join(' and ')] unless filters.empty?
  p = IO.popen(cmd)
  output = p.read
  p.close
  assert_equal(0, $CHILD_STATUS.exitstatus, 'tshark exited with and error')
  connections = JSON.parse(output).map do |entry|
    connection_info(entry['_source']['layers'])
  end
  connections.uniq.map { |c| OpenStruct.new(c) }
end

class FirewallAssertionFailedError < Test::Unit::AssertionFailedError
end

# These assertions are made from the perspective of the system under
# testing when it comes to the concepts of "source" and "destination".
def assert_all_connections(pcap_file,
                           message: 'Unexpected connections were made',
                           **opts, &block)
  all = pcap_connections_helper(pcap_file, **opts)
  good = all.select(&block)
  bad = all - good
  return if bad.empty?

  raise FirewallAssertionFailedError,
        "#{message}\n" +
        bad.map { |e| "  #{e}" }.join("\n")
end

def assert_no_connections(pcap_file, **opts, &block)
  assert_all_connections(pcap_file, **opts) { |*args| !block.call(*args) }
end

def assert_no_leaks(pcap_file, allowed_hosts, allowed_dns_queries, **opts)
  assert_all_connections(pcap_file, **opts) do |c|
    allowed_hosts.include?({ address: c.daddr, port: c.dport })
  end

  # yes, we could combine these two checks in a single one,
  # and that would probably be more efficient.
  # However, we're gaining something when it comes to debugging:
  # the line number now tells you *which* check  has failed
  dns_opts = opts.clone
  dns_opts[:message] = 'Unexpected DNS queries were made'
  assert_all_connections(pcap_file, **dns_opts) do |c|
    !defined?(c.dns_question) ||
      c.dns_question.all? { |q| allowed_dns_queries.include?(q) }
  end
end

def debug_useless_dns_exceptions(pcap_file, allowed_dns_queries)
  queries_made = Set.new
  pcap_connections_helper(pcap_file) do |c|
    next unless defined?(c.dns_question)

    queries_made += c.dns_question
  end
  queries_allowed = Set.new(allowed_dns_queries)
  useless_dns_exceptions = queries_allowed - queries_made
  return if useless_dns_exceptions.empty?

  info_log(
    'Warning: these queries were allowed but not needed: ' \
    "#{useless_dns_exceptions.to_a}"
  )
end
