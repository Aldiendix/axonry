"""PR review: a separate module, run by a separate (scheduled) reviewer session. It never touches the orchestrator's
state: it reads the public status.json, inspects PR content on GitHub, and posts its verdict as a GitHub review on
the exact commit. The orchestrator reads those reviews (authorized logins: the season's review.reviewers) and queues
only content whose digest was approved. Order: the review queue is the public priority order (the chain position of
each content's commitment); the reviewer takes it top-down and cannot skip ahead (see cli.py). Policy: POLICY.md.
"""
