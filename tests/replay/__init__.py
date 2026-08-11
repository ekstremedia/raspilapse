"""Golden-master replay of the exposure controller.

The refactor moves every module in this project. These tests exist to prove
that moving them changed no exposure decision: a recorded sequence of light
measurements goes in, and the exact settings the controller produced before
the move must come out.
"""
