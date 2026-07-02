# core/otp/service/otpservice.py

import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import smtplib
import ssl
from email.message import EmailMessage
from sqlalchemy.orm import Session
from core.otp.model.otp import OTP
from core.otp.dto.response.otp_send_response import OTPSendResponse
from core.wirepick.service.wirepickservice import WirepickSMSService, WirepickSMSException
from config import settings
import logging

logger = logging.getLogger(__name__)


class OTPService:
    def __init__(self, db: Session):
        self.db = db
        self.sms_service = WirepickSMSService()

    def generate_otp(self) -> str:
        """Generate a 5-digit OTP"""
        return ''.join(random.choices(string.digits, k=5))

    def _format_otp_message(self, otp_code: str) -> str:
        """Format OTP message for SMS"""
        return f"Your verification code is: {otp_code}. Valid for {settings.OTP_EXPIRE_SECONDS} seconds."

    def send_otp_phone(self, phone: str) -> OTPSendResponse:
        """Send OTP to phone number using Wirepick SMS"""
        try:
            otp_code = self.generate_otp()
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_EXPIRE_SECONDS)
            
            # Delete any existing OTP for this phone
            self.db.query(OTP).filter(OTP.phone == phone).delete()
            
            # Create new OTP record
            otp_record = OTP(
                phone=phone,
                otp=otp_code,
                expires_at=expires_at
            )
            
            self.db.add(otp_record)
            self.db.commit()
            self.db.refresh(otp_record)
            
            # Format the OTP message
            message = self._format_otp_message(otp_code)

            try:
                sms_result = self.sms_service.send_sms(phone, message)
                
                if sms_result.get('success'):
                    logger.info(f"OTP sent successfully to {phone}. Message ID: {sms_result.get('msgid')}")
                    
                    # You can store the msgid in your OTP record if needed
                    # otp_record.external_id = sms_result.get('msgid')
                    # self.db.commit()
                    
                    return OTPSendResponse(
                        success=True,
                        message="OTP sent successfully to your phone",
                        data={
                            "phone": phone,
                            "expires_at": expires_at.isoformat(),
                            "message_id": sms_result.get('msgid')  # Optional: return message ID for tracking
                        }
                    )
                else:
                    # SMS sending failed, rollback OTP creation
                    self.db.delete(otp_record)
                    self.db.commit()
                    
                    error_msg = sms_result.get('error', 'Unknown SMS error')
                    logger.error(f"Failed to send OTP via Wirepick: {error_msg}")
                    
                    return OTPSendResponse(
                        success=False,
                        message=f"Failed to send OTP. SMS provider error."
                    )
                    
            except WirepickSMSException as e:
                # SMS service error, rollback OTP creation
                self.db.delete(otp_record)
                self.db.commit()
                
                logger.error(f"Wirepick SMS error for {phone}: {str(e)}")
                return OTPSendResponse(
                    success=False,
                    message="Failed to send OTP. Please try again later."
                )
            
        except Exception as e:
            logger.error(f"Error sending OTP to phone {phone}: {str(e)}")
            return OTPSendResponse(
                success=False,
                message="Failed to send OTP. Please try again."
            )

    def send_otp_email(self, email: str) -> OTPSendResponse:
        """Send OTP to email address"""
        try:
            otp_code = self.generate_otp()
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_EXPIRE_SECONDS)
            
            # Delete any existing OTP for this email
            self.db.query(OTP).filter(OTP.email == email).delete()
            
            # Create new OTP record
            otp_record = OTP(
                email=email,
                otp=otp_code,
                expires_at=expires_at
            )
            
            self.db.add(otp_record)
            self.db.commit()
            self.db.refresh(otp_record)

            subject = "Your Viin verification code"
            body = f"Your verification code is: {otp_code}. Valid for {settings.OTP_EXPIRE_SECONDS} seconds."

            smtp_host = settings.ZEPTOMAIL_SMTP_HOST
            smtp_port = settings.ZEPTOMAIL_SMTP_PORT
            smtp_username = settings.ZEPTOMAIL_SMTP_USERNAME
            smtp_password = settings.ZEPTOMAIL_SMTP_PASSWORD

            sender_domain = os.getenv("ZEPTOMAIL_SENDER_DOMAIN", "useviin.com").strip()
            from_email = settings.ZEPTOMAIL_FROM_EMAIL or f"no-reply@{sender_domain}"

            if not smtp_password:
                # Rollback OTP creation if we cannot send
                self.db.delete(otp_record)
                self.db.commit()
                logger.error("ZEPTOMAIL_SMTP_PASSWORD/ZEPTOMAIL_API_TOKEN not set; cannot send OTP email")
                return OTPSendResponse(success=False, message="Email service not configured")

            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = from_email
            msg["To"] = email
            msg.set_content(body)

            try:
                if smtp_port == 465:
                    context = ssl.create_default_context()
                    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as server:
                        server.login(smtp_username, smtp_password)
                        server.send_message(msg)
                else:
                    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                        server.starttls()
                        server.login(smtp_username, smtp_password)
                        server.send_message(msg)

                logger.info(f"OTP email sent successfully to {email}")
                return OTPSendResponse(
                    success=True,
                    message="OTP sent successfully to your email",
                    data={"email": email, "expires_at": expires_at.isoformat()},
                )
            except Exception as e:
                # Rollback OTP creation if email send fails
                self.db.delete(otp_record)
                self.db.commit()
                logger.error(f"Failed to send OTP email to {email}: {e}")
                return OTPSendResponse(success=False, message="Failed to send OTP email. Please try again.")
            
        except Exception as e:
            logger.error(f"Error sending OTP to email {email}: {str(e)}")
            return OTPSendResponse(
                success=False,
                message="Failed to send OTP. Please try again."
            )

    def validate_otp(self, phone: Optional[str] = None, email: Optional[str] = None, otp: str = None) -> bool:
        """Validate OTP for phone or email"""
        try:
            if not otp:
                return False
            
            # Build query based on what's provided
            query = self.db.query(OTP).filter(OTP.otp == otp)
            
            if phone:
                query = query.filter(OTP.phone == phone)
            elif email:
                query = query.filter(OTP.email == email)
            else:
                return False
            
            otp_record = query.first()
            
            if not otp_record:
                return False
            
            # Check if OTP has expired
            if otp_record.is_expired():
                # Clean up expired OTP
                self.db.delete(otp_record)
                self.db.commit()
                return False
            
            # OTP is valid, delete it to prevent reuse
            self.db.delete(otp_record)
            self.db.commit()
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating OTP: {str(e)}")
            return False

    def cleanup_expired_otps(self):
        """Clean up expired OTP records"""
        try:
            current_time = datetime.now(timezone.utc)
            expired_count = self.db.query(OTP).filter(OTP.expires_at < current_time).delete()
            self.db.commit()
            logger.info(f"Cleaned up {expired_count} expired OTP records")
        except Exception as e:
            logger.error(f"Error cleaning up expired OTPs: {str(e)}")