# When creating this issue

 - [ ] Set the milestone to the upcoming release
 - [ ] Delete this section

# How

1. Checkout the tag for the last released

2. Run the test suite on the last released images (passed via `--iso`)

   We want the test suite run to be as "vanilla" and reproducible as
   possible, so the only allowed options for `run_test_suite` are:

    - `--artifacts-base-uri`
    - `--capture` and `--capture-all`
    - `--tmpdir`
    - `--view`
    - `--vnc-server-only`
    - `--iso` and `--old-iso` (in fact, both of these are mandatory)

   For `cucumber` itself we only allow the `--format` option.

# Why?

In order to streamline our release process, we've removed "running our test
suite locally" from the critical path of our release QA. Instead we now rely on
the test suite run on Jenkins.

We don't fully trust Jenkins, so this makes us vulnerable to an attack that
makes Jenkins report success while it should report failure.

We assume that exploiting our release QA via Jenkins requires being aware of
a flaw in Tails that the suite would spot during the release process but that we
would not notice via usual development. So we decided to set up safeguards to
ensure we would eventually notice such a flaw, to decrease the incentive to use
this attack: this helps us build trust that Jenkins is not lying, and shrinks
the opportunity window for an attacker.

/title "Run the automated test suite on a local trusted system"
/label ~"Tails Team"
/label ~"To Do"
/label ~"P:Elevated"
/label ~"C:Test Suite"
/label ~"T:Test"
