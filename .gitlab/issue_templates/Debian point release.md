# When creating this issue

- [ ] Set the title to "Upgrade to Trixie `$POINT_RELEASE_VERSION`"
- [ ] Set the start date to the planned release date for the Debian point release
- [ ] Set the due date to the Tuesday that follows the start date
- [ ] If a suitable milestone exists already, set it
- [ ] Delete this section

# Process

1. Set environment variables:

        WORK_ITEM=1234
        POINT_RELEASE_VERSION=42.1

2. Create the MR:

        ./bin/create-Debian-point-release-MR "${WORK_ITEM:?}" "${POINT_RELEASE_VERSION:?}"

3. Submit your MR for review via our usual process.

# References

- https://release.debian.org/#point-releases

/label ~"Tails Team"
/label ~"To Do"
/label ~"T:Wait"
