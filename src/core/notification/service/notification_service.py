from datetime import datetime
import secrets
import string
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from core.notification.model.Notification import Notification, NotificationStatus, NotificationType
from core.user.model.User import User
from core.user.notification_preferences import allows_in_app_notifications, allows_sms_notifications
from core.moolre.service.moolreservice import MoolreException
from core.sms.service.sms_factory import get_sms_service
from config import settings

# DTO Models
from core.notification.dto.response.notification_response import NotificationResponse
from core.notification.dto.response.paged_notifications import PagedNotificationResponse
from core.notification.dto.response.message_response import MessageResponse
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
        self.sms_service = get_sms_service()
        self.sms_enabled = getattr(settings, 'SMS_NOTIFICATION_ENABLED', True)

    def _format_sms_message(self, notification_type: NotificationType, data: dict) -> str:
        """Format notification content for SMS based on type and data"""
        message_template = data.get('message', '')
        
        # If message is provided in data, use it
        if message_template:
            return message_template[:160]  # SMS length limit
        
        # Otherwise, create a default message based on notification type
        type_messages = {
            NotificationType.INFO: "Info: {content}",
            NotificationType.WARNING: "Warning: {content}",
            NotificationType.ERROR: "Error: {content}",
            NotificationType.SUCCESS: "Success: {content}",
            NotificationType.PROMOTIONAL: "Special Offer: {content}",
            NotificationType.TRANSACTIONAL: "Transaction Update: {content}",
            NotificationType.OTP: "Your verification code is: {otp}. Valid for {expiry} minutes.",
            NotificationType.ALERT: "Alert: {content}"
        }
        
        template = type_messages.get(notification_type, "{content}")
        
        # Replace placeholders with actual data
        content = data.get('content', 'You have a new notification')
        expiry = data.get('expiry_minutes', 5)
        otp = data.get('otp', '')
        
        message = template.format(
            content=content,
            expiry=expiry,
            otp=otp,
            **data
        )
        
        return message[:160]  # Ensure SMS length limit

    def create_notification(
        self,
        user_id: str,
        notification_type: NotificationType,
        data: dict,
        send_sms: bool = True,
        sms_phone: Optional[str] = None
    ) -> NotificationResponse:
        """Create a new notification for a user and optionally send SMS"""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not allows_in_app_notifications(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="In-app notifications are disabled for this user",
            )

        # Determine SMS phone number (use provided or from user profile)
        sms_phone_to_use = sms_phone or getattr(user, 'phone', None)
        should_send_sms = send_sms and allows_sms_notifications(user)
        
        # Create notification record
        notification = Notification(
            id=self._generate_notification_id(),
            user_id=user_id,
            type=notification_type,
            data=data,
            status=NotificationStatus.UNREAD,
            sms_sent=False,
            sms_phone=sms_phone_to_use
        )

        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)

        # Send SMS if enabled, opted in, and phone number available
        if should_send_sms and self.sms_enabled and sms_phone_to_use:
            try:
                self._send_sms_notification(notification.id, sms_phone_to_use, notification_type, data)
            except Exception as e:
                logger.error(f"Failed to send SMS for notification {notification.id}: {str(e)}")
                # Notification still created even if SMS fails

        return NotificationResponse.from_orm(notification)

    def _send_sms_notification(self, notification_id: str, phone: str, 
                               notification_type: NotificationType, data: dict) -> None:
        """Send SMS notification and update notification record"""
        try:
            # Format SMS message
            message = self._format_sms_message(notification_type, data)
            
            # Send via Moolre
            result = self.sms_service.send_sms(phone, message)
            
            # Update notification with SMS details
            notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
            if notification:
                notification.sms_sent = result.get('success', False)
                notification.sms_message_id = result.get('msgid')
                notification.sms_status = result.get('status')
                notification.sms_sent_at = datetime.now()
                
                if result.get('success'):
                    logger.info(f"SMS sent successfully for notification {notification_id}, msgid: {result.get('msgid')}")
                else:
                    notification.status = NotificationStatus.FAILED
                    logger.error(f"SMS failed for notification {notification_id}: {result.get('error')}")
                
                self.db.commit()
                
        except MoolreException as e:
            # Update notification with failure
            notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
            if notification:
                notification.sms_sent = False
                notification.sms_status = "FAILED"
                notification.status = NotificationStatus.FAILED
                self.db.commit()
            
            logger.error(f"Moolre SMS error for notification {notification_id}: {str(e)}")
            raise

    def send_bulk_sms_notifications(self, user_ids: List[str], message: str, 
                                    notification_type: NotificationType = NotificationType.INFO,
                                    data: Optional[dict] = None) -> Dict[str, Any]:
        """Send bulk SMS notifications to multiple users"""
        results = {
            "total": len(user_ids),
            "successful": 0,
            "failed": 0,
            "notifications": []
        }
        
        for user_id in user_ids:
            try:
                user = self.db.query(User).filter(User.id == user_id).first()
                if user and allows_sms_notifications(user) and hasattr(user, 'phone') and user.phone:
                    notification_data = data or {}
                    notification_data['message'] = message
                    
                    notification = self.create_notification(
                        user_id=user_id,
                        notification_type=notification_type,
                        data=notification_data,
                        send_sms=True,
                        sms_phone=user.phone
                    )
                    
                    results["successful"] += 1
                    results["notifications"].append(notification)
                else:
                    logger.warning(f"User {user_id} has no phone number, skipping SMS")
                    results["failed"] += 1
                    
            except Exception as e:
                logger.error(f"Failed to send SMS to user {user_id}: {str(e)}")
                results["failed"] += 1
        
        return results

    def get_notification(self, notification_id: str) -> NotificationResponse:
        """Get a specific notification by ID"""
        notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        return NotificationResponse.from_orm(notification)

    def get_user_notifications_paged(
        self,
        user_id: str,
        page: int,
        size: int,
        status: Optional[NotificationStatus] = None,
        notification_type: Optional[NotificationType] = None
    ) -> PagedNotificationResponse:
        """Get paginated notifications for a user with optional filters"""
        query = self.db.query(Notification).filter(Notification.user_id == user_id)

        if status:
            query = query.filter(Notification.status == status)
        if notification_type:
            query = query.filter(Notification.type == notification_type)

        total = query.count()
        notifications = query.order_by(Notification.created_at.desc()) \
                           .offset((page - 1) * size) \
                           .limit(size) \
                           .all()

        return PagedNotificationResponse(
            items=[NotificationResponse.from_orm(n) for n in notifications],
            total=total,
            page=page,
            size=size,
            pages=(total + size - 1) // size  # Calculate total pages
        )

    def update_notification(
        self,
        notification_id: str,
        status: Optional[NotificationStatus] = None,
        data: Optional[dict] = None
    ) -> NotificationResponse:
        """Update a notification's status and/or data"""
        notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        if status is not None:
            notification.status = status
            if status == NotificationStatus.READ and notification.read_at is None:
                notification.read_at = datetime.now()

        if data is not None:
            notification.data = data

        self.db.commit()
        self.db.refresh(notification)

        return NotificationResponse.from_orm(notification)

    def mark_notification_as_read(self, notification_id: str) -> NotificationResponse:
        """Mark a specific notification as read"""
        return self.update_notification(
            notification_id=notification_id,
            status=NotificationStatus.READ
        )

    def mark_all_notifications_as_read(self, user_id: str) -> MessageResponse:
        """Mark all unread notifications for a user as read"""
        self.db.query(Notification) \
             .filter(Notification.user_id == user_id) \
             .filter(Notification.status == NotificationStatus.UNREAD) \
             .update({
                 Notification.status: NotificationStatus.READ,
                 Notification.read_at: datetime.now()
             }, synchronize_session=False)
        
        self.db.commit()
        return MessageResponse(message="All notifications marked as read")

    def delete_notification(self, notification_id: str) -> None:
        """Delete a notification"""
        notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")

        self.db.delete(notification)
        self.db.commit()
    
    def check_sms_delivery_status(self, notification_id: str) -> Dict[str, Any]:
        """Check SMS delivery status for a notification"""
        notification = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if not notification:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        if not notification.sms_message_id:
            return {
                "notification_id": notification_id,
                "sms_sent": notification.sms_sent,
                "message": "No SMS was sent for this notification"
            }
        
        try:
            status_result = self.sms_service.check_message_status(notification.sms_message_id)
            
            # Update notification with delivery status
            if status_result.get('success'):
                notification.sms_delivery_status = status_result.get('status')
                if status_result.get('status') == 'DLV':  # Delivered
                    notification.sms_delivered_at = datetime.now()
                    notification.status = NotificationStatus.DELIVERED
                self.db.commit()
            
            return {
                "notification_id": notification_id,
                "sms_message_id": notification.sms_message_id,
                "delivery_status": status_result,
                "current_status": notification.sms_delivery_status
            }
            
        except Exception as e:
            logger.error(f"Failed to check SMS status for {notification_id}: {str(e)}")
            return {
                "notification_id": notification_id,
                "error": str(e)
            }
    
    def _generate_notification_id(self) -> str:
        """Generate a unique notification ID"""
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for i in range(16))