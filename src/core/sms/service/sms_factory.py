from core.moolre.service.moolreservice import MoolreSMSService


def get_sms_service():
    """Return the Moolre SMS provider."""
    return MoolreSMSService()
