# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4

import re

from datetime import datetime, timedelta
from operator import itemgetter
from tailslib.git import Git

# These will result in "fake" custom branches. Each member results in
# a copy of the active branch with "project_name" equal to
# "base_project_name", with the other key-value pairs overwritten by
# those in the custom branch's dictionary.
custom_branches = [
    {
        "base_project_name": "stable",
        "project_name": "stable-flatpak",
        "recipients": "whoever_broke_the_build",
        "custom_config": {
            "default_extra_suite_args": " --extra-boot-options flatpak ",
            "pollscm_cron": "",
        },
    }
]


class ActiveBranches(Git):
    """
    Compute set of active Git branches that should be built on Jenkins,
    along with some parameters that the Jenkins build jobs would need.
    """

    # These branches are always considered as worthy to be built.
    ALWAYS_ACTIVE_BRANCHES = (
        "devel",
        "stable",
    )

    # These branches are used for the "has this topic branch been
    # merged already?" logic. The "base branch" concept here is
    # different from its homonyms (e.g. what's in config/base_branch,
    # or the base APT suite concept).
    BASE_BRANCHES = (
        "devel",
        "master",
        "stable",
        "testing",
    )

    def __init__(self, git_repo, active_days):
        # A branch that has not seen activity since this number of days
        # is not active.
        Git.__init__(self, git_repo)
        self.active_days = active_days
        assert isinstance(self.active_days, int)

    def compute(self):
        git_branches = [self.job_params(branch) for branch in self.active_branches()]
        all_branches = git_branches.copy()
        for custom_branch in custom_branches:
            new_branch = next(
                b.copy()
                for b in git_branches
                if b["project_name"] == custom_branch["base_project_name"]
            )
            new_branch.update(custom_branch)
            del new_branch["base_project_name"]
            all_branches.append(new_branch)
        return sorted(all_branches, key=itemgetter("branch_name"))

    def job_params(self, branch):
        return {
            "branch_name": branch,
            "project_name": self.project_name(branch),
            "recipients": self.recipients(branch),
            "is_base_branch": self.is_base_branch(branch),
            "ticket_number": self.__class__.ticket_number(branch),
        }

    def active_branches(self):
        since = int((datetime.now() - timedelta(days=self.active_days)).timestamp())
        branches = list(
            set(
                list(ActiveBranches.ALWAYS_ACTIVE_BRANCHES)
                + [
                    branch
                    for branch in self.all_branches()
                    if self.has_commits_since(since, branch)
                    and self.not_merged_into_any_base_branch(branch)
                ]
            )
        )
        if self.new_revs_in_branch("stable", "testing"):
            branches.append("testing")
        return sorted(branches)

    def is_base_branch(self, branch):
        return branch in ActiveBranches.BASE_BRANCHES

    def project_name(self, branch):
        project_name_re_match = re.compile("[^a-zA-Z0-9-.]")
        return project_name_re_match.sub("-", branch).lower()

    def recipients(self, branch):
        if self.is_base_branch(branch):
            return "release_managers"
        else:
            return "whoever_broke_the_build"

    @staticmethod
    def ticket_number(branch):
        """
        >>> ActiveBranches.ticket_number("bugfix/12345-foo")
        '12345'
        >>> ActiveBranches.ticket_number('bugfix/fix-this-and-that')
        0
        >>> ActiveBranches.ticket_number('bugfix/fix-this-and-that-42')
        0

        GitLab style
        >>> ActiveBranches.ticket_number('12345-do-that')
        '12345'
        """
        ticket_re_match = re.search(r"(?:^|/)(\d{4,6})+", branch)
        if ticket_re_match:
            return ticket_re_match.group(1)
        else:
            return 0

    def not_merged_into_any_base_branch(self, branch):
        return all(
            self.new_revs_in_branch(base_branch, branch)
            for base_branch in ActiveBranches.BASE_BRANCHES
        )
