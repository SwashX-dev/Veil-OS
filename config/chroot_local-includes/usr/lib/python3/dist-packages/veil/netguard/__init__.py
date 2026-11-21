"""Veil Network Guard: privacy monitoring and leak auditing.

Network Guard only *inspects* and *reports*. It never relaxes the Tails
firewall. Enforcement stays with the unchanged Tails ferm ruleset; Veil adds
kernel-level isolation (empty network namespaces) for its own components.
"""
