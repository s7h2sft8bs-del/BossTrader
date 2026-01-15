def risk_gate(user, proposal) -> tuple[bool, str]:
    """
    Return (allowed, reason).

    Hook TopstepX rules here later:
    - max daily loss
    - trailing drawdown behavior
    - max contracts / position size
    - time windows
    - cooldown rules
    - etc.
    """
    return True, "OK"
