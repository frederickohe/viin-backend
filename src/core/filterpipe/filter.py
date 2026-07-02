from typing import Optional, Dict, Any


class FilterPipeline:
    """Sequential filter pipeline for incoming messages.

    Methods perform independent checks and return a unified result dict from
    `process()` which the caller can use to decide whether to continue.
    """

    def __init__(self, db):
        self.db = db

    def check_user_exists(self, userid: str) -> Dict[str, Any]:
        from core.user.model.User import User

        user = self.db.query(User).filter(User.phone == userid).first()
        if not user:
            return {"ok": False, "message": "User not found. Please ensure you are registered."}
        return {"ok": True, "user": user}

    def check_subscription_active(self, user) -> Dict[str, Any]:
        from core.subscription.service.subscription_service import SubscriptionService

        service = SubscriptionService(self.db)
        try:
            result = service.get_user_subscription_status(user.phone)
            has_active = bool(result.get("has_active_subscription", False))
            return {"ok": True, "has_active_subscription": has_active}
        except Exception as e:
            return {"ok": False, "message": f"Error checking subscription: {e}"}

    def check_context_matches_agent(self, user, context: Optional[str]) -> Dict[str, Any]:
        # user.agents is expected to be a dict stored in the User.agents JSONB column
        agents = user.agents or {}

        if not context:
            # if the user only has one configured agent, use it as a default
            if len(agents) == 1:
                agent_name, agent_config = next(iter(agents.items()))
                return {"ok": True, "agent_name": agent_name, "agent_config": agent_config}
            return {"ok": False, "message": "Context (agent key) is required."}

        agent_config = agents.get(context)
        if agent_config is None:
            return {"ok": False, "message": f"No agent named '{context}' found for this user."}
        return {"ok": True, "agent_name": context, "agent_config": agent_config}

    def check_agent_params_complete(self, agent_name: str, agent_config: Dict[str, Any]) -> Dict[str, Any]:
        # Accept several possible ways agent authors might declare required keys
        required = agent_config.get("required_params") or agent_config.get("required_keys") or []

        # Fallback to central registry if agent config doesn't declare required keys
        if not required:
            try:
                from core.agent.agent_params import AGENT_REQUIRED_PARAMS
                required = AGENT_REQUIRED_PARAMS.get(agent_name, [])
            except Exception:
                required = []

        if not required:
            # Nothing to validate
            return {"ok": True}

        # Where parameters may be stored
        params = agent_config.get("params") or agent_config.get("config") or agent_config

        missing = [k for k in required if k not in params or params.get(k) in (None, "")]
        if missing:
            return {"ok": False, "message": f"Missing agent parameters for '{agent_name}': {', '.join(missing)}"}

        return {"ok": True}

    def process(self, userid: str, message: str, context: Optional[str]) -> Dict[str, Any]:
        """Run full filter pipeline and, if successful, dispatch to the Viin agent.

        Previously the webhooks controller was responsible for instantiating
        :class:`core.agent.agent.AutoBus` and invoking ``process_user_message``.
        The pipeline now performs that step so callers simply ask for the
        processed response in one go.

        Returns a dict with a boolean ``proceed`` flag.  When ``proceed`` is
        ``False`` there will be a ``message`` key containing a user-facing
        error.  On success ``response`` will contain the text produced by the
        agent.  Other information (``user``, ``has_active_subscription`` etc.)
        is still included in case callers need it later.
        """
        # 1. User exists
        res = self.check_user_exists(userid)
        if not res.get("ok"):
            return {"proceed": False, "message": res.get("message")}
        user = res.get("user")

        # 2. Subscription active
        res = self.check_subscription_active(user)
        if not res.get("ok"):
            return {"proceed": False, "message": res.get("message")}
        has_active = res.get("has_active_subscription", False)

        # 3. Context matches agent key
        res = self.check_context_matches_agent(user, context)
        if not res.get("ok"):
            return {"proceed": False, "message": res.get("message")}
        agent_name = res.get("agent_name")
        agent_config = res.get("agent_config")

        # 4. Agent params complete
        # res = self.check_agent_params_complete(agent_name, agent_config)
        # if not res.get("ok"):
        #     return {"proceed": False, "message": res.get("message")}

        # All checks passed -> dispatch to AutoBus
        try:
            from core.agent.agent import AutoBus
        except ImportError:
            # Should never happen as AutoBus is a core component
            return {
                "proceed": False,
                "message": "Agent initialization failed."
            }

        agent = AutoBus(db_session=self.db)
        # AutoBus.process_user_message currently only requires user id, message,
        # and subscription status.  Additional agent-specific configuration is
        # stored and validated earlier in the pipeline but not yet consumed by
        # the core agent implementation.
        response_message = agent.process_user_message(
            userid,
            message,
            agent_name,
        )

        return {
            "proceed": True,
            "response": response_message,
            "user": user,
            "has_active_subscription": has_active,
            "agent_name": agent_name,
            "agent_config": agent_config,
        }
