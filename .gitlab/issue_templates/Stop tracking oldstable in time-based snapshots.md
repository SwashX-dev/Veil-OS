# When creating this issue

* [ ] Set the milestone to the upcoming N.0 release, where we'll update to Debian `$CODENAME`
* [ ] Set the title to *Stop tracking $DIST in time-based snapshots*, with `$DIST` being the codename for the version of Debian that Tails N.0 will upgrade *from*; at the time you're creating this issue, `$DIST` is probably the current Debian *stable*; and at the time we'll do the work, `$DIST` will probably have be Debian *olstable*
* [ ] Delete this section

# How

* [ ] Identify blockers and add them in the relevant place on this checklist
* [ ] Set a suitable milestone (most likely we won't do this before N.0 is out)
* [ ] Release Tails N.0 so we don't use oldstable anymore
* [ ] Add the relevant "GitLab CI: use Debian XXX containers" issue here
* [ ] Verify we don't use oldstable anywhere else anymore
* [ ] [other blockers]
* [ ] https://tails.net/contribute/APT_repository/time-based_snapshots/#stop-tracking-a-distribution

# Why

Maintaining snapshots of distributions we don't use anymore has several
drawbacks:

- It makes some reprepro operations *very* slow:
  https://gitlab.tails.net/tails/blueprints/-/wikis/freezable_APT_repository#handle-ever-growing-referencesdb
- It uses disk space needlessly.

/label ~"Tails Team"
/label ~"To Do"
/label ~"C:Infrastructure"
