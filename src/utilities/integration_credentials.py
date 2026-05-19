# Chatwoot Devise policy: upper, lower, digit, special. Appended to the username core.
CHATWOOT_PASSWORD_POLICY_SUFFIX = "Aa1!"


def integration_local_password(*, username: str) -> str:
    """
    LOCAL password for hosted Postiz accounts provisioned by Autobus.

    Sign in with the user's email and this value (Autobus ``fullname``, exposed as
    username in the API). It is intentionally independent of the Autobus login
    password so backend password resets do not break integration SSO-style login.
    """
    pwd = (username or "").strip()
    if not pwd:
        raise ValueError("username is required for integration local password")
    return pwd


def integration_chatwoot_password(*, username: str) -> str:
    """
    Chatwoot LOCAL password: ``fullname`` + ``CHATWOOT_PASSWORD_POLICY_SUFFIX``.

    The suffix satisfies Chatwoot's complexity rules while keeping the Autobus
    username as the memorable core (e.g. ``Jane Doe`` → ``Jane DoeAa1!``).
    """
    return f"{integration_local_password(username=username)}{CHATWOOT_PASSWORD_POLICY_SUFFIX}"
