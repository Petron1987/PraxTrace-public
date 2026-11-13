"""Business evidence and continuous verification for AI agents."""
__version__ = "0.1.0a1"
def check_record(*args, **kwargs):
    from .execution_record import check_record as check
    return check(*args, **kwargs)
def review_evidence(*args, **kwargs):
    from .evidence_review import review
    return review(*args, **kwargs)
