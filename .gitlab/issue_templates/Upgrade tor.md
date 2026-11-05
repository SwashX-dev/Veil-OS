# When creating this issue

 - [ ] Set the title to "Upgrade to tor `$TOR_VERSION`"
 - [ ] Set the correct milestone
 - [ ] Delete this section

# Process

1. Wait for the new version to be available in our APT snapshots

2. Set environment variables:

        TOR_VERSION=FIXME
        TOR_WORK_ITEM=FIXME

2. Create the MR:

        ./bin/create-tor-MR "${TOR_WORK_ITEM:?}" "${TOR_VERSION:?}"

3. Submit your MR for review via our usual process.

# References

- https://tails.net/contribute/tor/

/label ~"Tails Team"
/label ~"To Do"
/label ~"P:Elevated"
/label ~"C:Tor Connection"
/label ~"T:Code"
