"""DELIBERATELY FAILING probe used to prove that a red pull request cannot be merged.
This file must never reach main. The pull request that carries it is closed unmerged."""


def test_gate_probe_is_deliberately_red():
    assert False, "deliberate failure: proves the required 'pytest' check blocks merging"
