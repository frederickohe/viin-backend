"""Auto-generated module that imports all SQLAlchemy model modules.

Importing these modules ensures their model classes are defined and
registered on `Base.metadata` so Alembic autogenerate can detect tables.

If any import fails, the exception is printed and import proceeds.
"""
from importlib import import_module
from utilities.dbconfig import Base

# List of model modules to import (module path relative to `src`)
_model_modules = [
	"core.user.model.User",
	"core.cloudstorage.model.filemodel",
	"core.subscription.model.user_subscription",
	"core.subscription.model.subscription_plan",
	"core.receipts.model.Receipt",
	"core.otp.model.otp",
	"core.notification.model.Notification",
	"core.nlu.model.Conversation",
	"core.histories.model.history",
	"core.auth.model.refreshtoken",
	"core.auth.model.password_reset_token",
	"core.paystack.model.transaction",
	"core.memory.model.source_message",
	"core.memory.model.memory_item",
	"core.memory.model.memory_list",
	"core.memory.model.reminder",
	"core.memory.model.share_grant",
	"core.memory.model.delivery_log",
 
]


for _m in _model_modules:
	try:
		import_module(_m)
	except Exception as _err:
		# Avoid crashing on import errors during alembic autogenerate; print for visibility
		print(f"dbmodels: failed to import {_m}: {_err}")
