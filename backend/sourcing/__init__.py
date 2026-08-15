"""First-party manufacturer source retrieval.

The brief's sourcing rule — product data must come from the manufacturer's own
site or documentation, never a marketplace or distributor — is implemented here
as four separable pieces:

* `policy`    who we may read, enforced fail-closed
* `discovery` locating a first-party URL for a part number
* `fetch`     polite, cached, robots-aware retrieval
* `extract`   page to specification table, prose and document links
* `evidence`  the retrieved material plus the citation that travels with it
"""

from backend.sourcing.evidence import Evidence, EvidenceBundle
from backend.sourcing.fetch import WebFetcher, get_fetcher, reset_fetcher

__all__ = [
    "Evidence",
    "EvidenceBundle",
    "WebFetcher",
    "get_fetcher",
    "reset_fetcher",
]
