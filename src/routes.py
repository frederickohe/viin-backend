from fastapi import APIRouter, Depends, HTTPException
from another_fastapi_jwt_auth import AuthJWT

from another_fastapi_jwt_auth.exceptions import MissingTokenError
import jwt

# Router for organizing routes
base_routes = APIRouter()

# Central function to handle token validation
def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401, detail="Token expired. Please log in again."
        )
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error validating token: {str(e)}")


# ROOT ROUTE
@base_routes.get("/")
def home():
    return {
        "message": "Welcome to Autobus Backend!",
        "description": "API backend for Autobus Financial Assistant Platform.",
        "default endpoints": [
            "Authentication",
            "File / Document Management",
            "Message and Task Queuing",
            "Notifications",
            "Media Generation (image + video via Google)",
        ],
        "note": "Pay attention to the API Documentation via README.md.",
    }