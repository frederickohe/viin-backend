from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from another_fastapi_jwt_auth import AuthJWT
import jwt
from sqlalchemy.orm import Session
from typing import List
from decimal import Decimal
import logging
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from datetime import datetime

from core.exceptions.PaymentException import PaymentNotFoundException
from core.payments.dto.paymentdto import PaymentDto
from core.payments.dto.response.pagedpaymentresponse import PagedPaymentResponse
from core.payments.dto.response.paymentcallbackresponse import PaymentCallbackResponse
from core.payments.dto.response.paymentresultresponse import PaymentResultResponse
from core.payments.model.paymentmethod import PaymentMethod
from core.payments.model.timeline import Timeline
from core.payments.service.paymentservice import PaymentService
from utilities.dbconfig import SessionLocal


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401, 
            detail="Token expired. Please log in again."
        )
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}"
        )

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
payment_routes = APIRouter()

@payment_routes.post("pay", response_model=PaymentResultResponse)
def create_payment(
    payment: PaymentDto,
    request: Request,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.make_payment(payment, request)

@payment_routes.get("/get-payment-by-id/{id}", response_model=PaymentDto)
def get_payment_by_id(
    id: int = Path(..., description="Payment ID"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_payment_by_id(id)

@payment_routes.get("/get-all-payment/{page}/{size}/{timeline}", response_model=PagedPaymentResponse)
def get_all_payments(
    page: int = Path(..., description="Page number"),
    size: int = Path(..., description="Page size"),
    timeline: Timeline = Path(..., description="Timeline filter"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_all_payments(page, size, timeline)


@payment_routes.get("/method/{payment_method}", response_model=List[PaymentDto])
def get_payments_by_method(
    payment_method: PaymentMethod = Path(..., description="Payment method"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_payments_by_method(payment_method)

@payment_routes.get("/revenue", response_model=Decimal)
def get_total_revenue(
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_total_revenue()

@payment_routes.get("/revenue/{timeline}", response_model=Decimal)
def get_total_revenue_within_timeline(
    timeline: Timeline = Path(..., description="Timeline filter"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_total_revenue_within_timeline(timeline)

@payment_routes.get("/service/{service_name}", response_model=List[PaymentDto])
def get_payments_by_service_name(
    service_name: str = Path(..., description="Service name"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_payments_by_service_name(service_name)

@payment_routes.get("/customer/{customer_name}", response_model=List[PaymentDto])
def get_payments_by_customer_name(
    customer_name: str = Path(..., description="Customer name"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    payment_service = PaymentService(db)
    return payment_service.get_payments_by_customer_name(customer_name)

@payment_routes.get("/status/{transaction_id}")
def get_payment_status(
    transaction_id: str = Path(..., description="Transaction ID to check status"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token)
):
    """
    Check the status of a payment by transaction ID.
    Useful when user wants to verify if their payment was completed.

    Returns:
    - PENDING: Payment request accepted, awaiting processing confirmation
    - SUCCESS: Payment completed successfully
    - FAILED: Payment failed
    """
    from core.payments.model.payment import Payment

    try:
        payment = db.query(Payment).filter(Payment.transaction_id == transaction_id).first()

        if not payment:
            raise HTTPException(
                status_code=404,
                detail=f"No payment found with transaction ID: {transaction_id}"
            )

        return {
            "transaction_id": payment.transaction_id,
            "payment_id": payment.id,
            "status": payment.status,
            "amount": payment.amount_paid,
            "payment_method": payment.payment_method,
            "created_at": payment.date_paid,
            "updated_at": payment.updated_on
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking payment status for transaction {transaction_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error checking payment status. Please try again."
        )

@payment_routes.post("/send-money")
def send_money_direct(
    amount: Decimal = Query(..., description="Amount in GHS to send"),
    phone: str = Query(..., description="Receiver phone number (0XXXXXXXXX or 233XXXXXXXXX)"),
    reference: str = Query("Direct Payout", description="Optional reference description"),
    db: Session = Depends(get_db)
):
    """
    Send money directly to a phone number using MTC (Merchant to Customer).
    No database records created - direct payout to Orchard API.
    Useful for payouts, refunds, or direct transfers.

    Args:
        amount: Amount in GHS to send
        phone: Receiver phone number (0XXXXXXXXX or 233XXXXXXXXX format)
        reference: Optional reference description

    Returns:
        - success: Boolean indicating if MTC was sent to gateway
        - transaction_id: MTC transaction ID
        - resp_code: Orchard response code (015 = accepted for processing)
        - message: Status message
        - receiver_phone: Phone number money was sent to
    """
    from utilities.uniqueidgenerator import UniqueIdGenerator
    from utilities.phone_utils import convert_to_local_ghana_format
    from core.customers.utility.network_detector import NetworkDetector

    try:
        # Validate inputs
        if not amount or amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Amount must be greater than 0"
            )

        if not phone:
            raise HTTPException(
                status_code=400,
                detail="Phone number is required"
            )

        logger.info(f"[SEND_MONEY_DIRECT] Direct MTC payout: Amount={amount}, Phone={phone}, Reference={reference}")

        # Generate transaction ID
        mtc_transaction_id = str(UniqueIdGenerator.generate())

        # Detect network from phone
        detected_network, network_message = NetworkDetector.detect_network_from_phone(phone)
        logger.info(f"[SEND_MONEY_DIRECT_NETWORK] Phone: {phone} -> Network: {detected_network} ({network_message})")

        # Build MTC request
        amount_decimal = Decimal(str(amount))
        mtc_request = {
            "amount": str(amount_decimal.quantize(Decimal('0.00'))),
            "customer_number": convert_to_local_ghana_format(phone),
            "exttrid": mtc_transaction_id,
            "nw": detected_network,
            "reference": reference,
            "service_id": "4892",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "callback_url": "https://viin-dcahe0a8cjecffcm.canadacentral-01.azurewebsites.netapi/api/v1/payment/callback",
            "trans_type": "MTC"
        }

        logger.info(f"[SEND_MONEY_DIRECT_REQUEST] Sending MTC request: {mtc_request}")

        # Send to Orchard API
        payment_service = PaymentService(db)
        response = payment_service.payment_gateway_client.process_payment(mtc_request)

        logger.info(f"[SEND_MONEY_DIRECT_RESPONSE] Orchard response: status_code={response.status_code}")

        if response.status_code == 200:
            response_data = response.json()
            resp_code = response_data.get("resp_code")

            return {
                "success": True,
                "message": f"Payout sent successfully (resp_code: {resp_code})",
                "transaction_id": mtc_transaction_id,
                "resp_code": resp_code,
                "resp_desc": response_data.get("resp_desc"),
                "amount": str(amount),
                "receiver_phone": convert_to_local_ghana_format(phone),
                "network": detected_network,
                "reference": reference,
                "timestamp": datetime.now().isoformat()
            }
        else:
            error_msg = response.text
            logger.error(f"[SEND_MONEY_DIRECT_ERROR] Gateway error: {error_msg}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Gateway error: {error_msg}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending money: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error sending money: {str(e)}"
        )

@payment_routes.post("/pay-bill")
def pay_bill_direct(
    amount: Decimal = Query(..., description="Amount in GHS to pay"),
    account_number: str = Query(..., description="Smart card/account number for bill"),
    network: str = Query(..., description="Telco biller network (GOT, DST, MPP, VPP, STT, VBB)"),
    reference: str = Query("Bill Payment", description="Optional reference description"),
    db: Session = Depends(get_db)
):
    """
    Pay telco bills directly using BLP (Bill Payment).
    Direct bill payment to Orchard API.
    Useful for paying bills to telco service providers.

    Args:
        amount: Amount in GHS to pay
        account_number: Smart card/account number (e.g., 95200204493)
        network: Telco biller network code (GOT, DST, MPP, VPP, STT, VBB)
        reference: Optional reference description

    Returns:
        - success: Boolean indicating if BLP was sent to gateway
        - transaction_id: BLP transaction ID
        - resp_code: Orchard response code (015 = accepted for processing)
        - message: Status message
        - account_number: Smart card number bill was paid for
        - network: Bill provider network
    """
    from utilities.uniqueidgenerator import UniqueIdGenerator

    try:
        # Validate inputs
        if not amount or amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Amount must be greater than 0"
            )

        if not account_number:
            raise HTTPException(
                status_code=400,
                detail="Account number is required"
            )

        if not network:
            raise HTTPException(
                status_code=400,
                detail="Network is required"
            )

        logger.info(f"[PAY_BILL_DIRECT] Direct BLP payment: Amount={amount}, Account={account_number}, Network={network}, Reference={reference}")

        # Generate transaction ID
        blp_transaction_id = str(UniqueIdGenerator.generate())

        # Build BLP request directly to Orchard API
        amount_decimal = Decimal(str(amount))
        blp_request = {
            "amount": str(amount_decimal.quantize(Decimal('0.00'))),
            "customer_number": account_number,
            "exttrid": blp_transaction_id,
            "nw": network.upper(),
            "reference": reference,
            "service_id": "4892",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "callback_url": "https://viin-dcahe0a8cjecffcm.canadacentral-01.azurewebsites.net/api/v1/payment/callback",
            "trans_type": "BLP"
        }

        logger.info(f"[PAY_BILL_DIRECT_REQUEST] Sending BLP request: {blp_request}")

        # Send to Orchard API
        payment_service = PaymentService(db)
        response = payment_service.payment_gateway_client.process_payment(blp_request)

        logger.info(f"[PAY_BILL_DIRECT_RESPONSE] Orchard response: status_code={response.status_code}")

        if response.status_code == 200:
            response_data = response.json()
            resp_code = response_data.get("resp_code")

            return {
                "success": True,
                "message": f"Bill payment sent successfully (resp_code: {resp_code})",
                "transaction_id": blp_transaction_id,
                "resp_code": resp_code,
                "resp_desc": response_data.get("resp_desc"),
                "amount": str(amount),
                "account_number": account_number,
                "network": network.upper(),
                "reference": reference,
                "timestamp": datetime.now().isoformat()
            }
        else:
            error_msg = response.text
            logger.error(f"[PAY_BILL_DIRECT_ERROR] Gateway error: {error_msg}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Gateway error: {error_msg}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error paying bill: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error paying bill: {str(e)}"
        )


@payment_routes.post("/pay-external-bill")
def pay_external_bill_direct(
    amount: Decimal = Query(..., description="Amount in GHS to pay"),
    account_number: str = Query(..., description="Customer account/reference number with the biller"),
    ext_biller_ref_id: str = Query(..., description="External biller ID from /ext-billers inquiry (e.g., D9C37F3D52)"),
    ext_biller_ref_type: str = Query(..., description="Biller category/type (e.g., Electricity, School Fees)"),
    reference: str = Query("External Bill Payment", description="Optional reference description"),
    db: Session = Depends(get_db)
):
    """
    Pay non-telco bills (ABS external billers) directly using BLP (Bill Payment).
    This endpoint is for utility bills, school fees, and other external billers that require ext_biller_ref_id.

    Before using this endpoint, you must:
    1. Call /ext-billers to get the list of available billers and their ext_biller_ref_id
    2. Call /ext-biller-invoice to get the customer's bill amount and details
    3. Use this endpoint to make the actual payment

    Args:
        amount: Amount in GHS to pay (from invoice inquiry)
        account_number: Customer account/reference number with the biller (e.g., 233242752911 for ECG)
        ext_biller_ref_id: Biller ID from /ext-billers inquiry (e.g., D9C37F3D52)
        ext_biller_ref_type: Biller category/type (e.g., Electricity, School Fees, Water)
        reference: Optional reference description

    Returns:
        - success: Boolean indicating if BLP was sent to gateway
        - transaction_id: BLP transaction ID
        - resp_code: Orchard response code (015 = accepted for processing)
        - message: Status message
        - account_number: Account number bill was paid for
        - ext_biller_ref_id: External biller identifier

    Example:
        POST /api/v1/payment/pay-external-bill?amount=150.00&account_number=233242752911&ext_biller_ref_id=D9C37F3D52&ext_biller_ref_type=Electricity
    """
    from utilities.uniqueidgenerator import UniqueIdGenerator

    try:
        # Validate inputs
        if not amount or amount <= 0:
            raise HTTPException(
                status_code=400,
                detail="Amount must be greater than 0"
            )

        if not account_number:
            raise HTTPException(
                status_code=400,
                detail="Account number is required"
            )

        if not ext_biller_ref_id:
            raise HTTPException(
                status_code=400,
                detail="External biller reference ID (ext_biller_ref_id) is required"
            )

        if not ext_biller_ref_type:
            raise HTTPException(
                status_code=400,
                detail="External biller reference type (ext_biller_ref_type) is required"
            )

        logger.info(f"[PAY_EXTERNAL_BILL_DIRECT] ABS BLP payment: Amount={amount}, Account={account_number}, BillerID={ext_biller_ref_id}, Type={ext_biller_ref_type}, Reference={reference}")

        # Generate transaction ID
        blp_transaction_id = str(UniqueIdGenerator.generate())

        # Build BLP request with ext_biller_ref_id for ABS external billers
        amount_decimal = Decimal(str(amount))
        blp_request = {
            "amount": str(amount_decimal.quantize(Decimal('0.00'))),
            "customer_number": account_number,
            "exttrid": blp_transaction_id,
            "nw": "ABS",  # External billers always use ABS network
            "reference": reference,
            "service_id": "4892",
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "callback_url": "https://viin-dcahe0a8cjecffcm.canadacentral-01.azurewebsites.net/api/v1/payment/callback",
            "trans_type": "BLP",
            "ext_biller_ref_id": ext_biller_ref_id,  # Required for external billers
            "ext_biller_ref_type": ext_biller_ref_type  # Biller category/type
        }

        logger.info(f"[PAY_EXTERNAL_BILL_DIRECT_REQUEST] Sending ABS BLP request: {blp_request}")

        # Send to Orchard API
        payment_service = PaymentService(db)
        response = payment_service.payment_gateway_client.process_payment(blp_request)

        logger.info(f"[PAY_EXTERNAL_BILL_DIRECT_RESPONSE] Orchard response: status_code={response.status_code}")

        if response.status_code == 200:
            response_data = response.json()
            resp_code = response_data.get("resp_code")

            return {
                "success": True,
                "message": f"External bill payment sent successfully (resp_code: {resp_code})",
                "transaction_id": blp_transaction_id,
                "resp_code": resp_code,
                "resp_desc": response_data.get("resp_desc"),
                "amount": str(amount),
                "account_number": account_number,
                "ext_biller_ref_id": ext_biller_ref_id,
                "ext_biller_ref_type": ext_biller_ref_type,
                "network": "ABS",
                "reference": reference,
                "timestamp": datetime.now().isoformat()
            }
        else:
            error_msg = response.text
            logger.error(f"[PAY_EXTERNAL_BILL_DIRECT_ERROR] Gateway error: {error_msg}")
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Gateway error: {error_msg}"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error paying external bill: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error paying external bill: {str(e)}"
        )


@payment_routes.post("/callback")
def handle_payment_callback(
    callback_response: PaymentCallbackResponse,
    db: Session = Depends(get_db)
):
    logger.debug(f"Callback payload: {callback_response}")

    if callback_response.trans_ref is None:
        logger.error("Missing trans_ref in callback")
        raise HTTPException(status_code=400, detail="trans_ref is required")

    try:
        payment_service = PaymentService(db)
        payment_service.process_payment_callback(callback_response)
        logger.info(f"Callback processed successfully for transaction: {callback_response.trans_ref}")

        # Send WhatsApp notification after successful callback processing
        from core.payments.model.payment import Payment
        payment = db.query(Payment).filter(
            (Payment.transaction_id == str(callback_response.trans_ref)) |
            (Payment.ctm_transaction_id == str(callback_response.trans_ref)) |
            (Payment.mtc_transaction_id == str(callback_response.trans_ref))
        ).first()

        if payment:
            from core.payments.model.paymentstatus import PaymentStatus

            status_code = callback_response.trans_status[:3] if callback_response.trans_status else None
            is_success = status_code == "000"

            # Only send notification if payment is not already in terminal state
            # This prevents duplicate notifications if background job already processed it
            if payment.status not in [PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.CTM_FAILED, PaymentStatus.MTC_FAILED]:
                payment_service.send_payment_notification(
                    payment,
                    is_success=is_success,
                    failure_reason=callback_response.message if not is_success else None
                )
                logger.info(f"[CALLBACK_NOTIFICATION] Notification sent for payment {payment.id}")
            else:
                logger.info(f"[CALLBACK_SKIP_NOTIFICATION] Payment {payment.id} already in terminal state {payment.status.name}, skipping duplicate notification")

            # Only stop the job if payment is in terminal state
            # If payment is in MTC_PROCESSING, ATP_PROCESSING, or BLP_PROCESSING, job must continue to check status
            if payment.status in [PaymentStatus.SUCCESS, PaymentStatus.FAILED, PaymentStatus.CTM_FAILED, PaymentStatus.MTC_FAILED, PaymentStatus.ATP_FAILED, PaymentStatus.BLP_FAILED]:
                from core.payments.service.payment_check_service import PaymentCheckService
                check_service = PaymentCheckService(db)
                check_service._stop_check_job(payment.id)
                logger.info(f"[CALLBACK_JOB_STOPPED] Background job stopped for payment {payment.id} - payment in terminal state {payment.status.name}")
            elif payment.status == PaymentStatus.MTC_PROCESSING:
                logger.info(f"[CALLBACK_JOB_CONTINUING] Payment {payment.id} now in MTC_PROCESSING state, job will continue to check MTC status")
            elif payment.status == PaymentStatus.ATP_PROCESSING:
                logger.info(f"[CALLBACK_JOB_CONTINUING] Payment {payment.id} now in ATP_PROCESSING state, job will continue to check ATP status")
            elif payment.status == PaymentStatus.BLP_PROCESSING:
                logger.info(f"[CALLBACK_JOB_CONTINUING] Payment {payment.id} now in BLP_PROCESSING state, job will continue to check BLP status")

        return {"message": "Callback processed successfully"}
    except PaymentNotFoundException as ex:
        logger.error(f"Payment not found for transaction: {callback_response.trans_ref}", exc_info=True)
        raise HTTPException(status_code=404, detail=f"Payment record not found for reference: {callback_response.trans_ref}")
    except ValueError as ex:
        logger.error(f"Invalid callback data: {ex}")
        raise HTTPException(status_code=400, detail=str(ex))
    except Exception as ex:
        logger.error("Unexpected error during callback processing", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process callback. Please try again or contact support.")

# Test endpoints for debugging/Postman testing
@payment_routes.get("/check-wallet-balance")
def check_wallet_balance(db: Session = Depends(get_db)):
    """
    Test endpoint to check merchant wallet balance.
    Returns wallet balance for all transaction types (payout, airtime, billpay, etc.)

    Response includes:
    - sms_bal: SMS balance
    - payout_bal: Balance available for MTC (money transfer)
    - billpay_bal: Balance for bill payments
    - available_collect_bal: Available collection balance
    - airtime_bal: Balance for airtime top-up
    - actual_collect_bal: Actual collection balance
    """
    try:
        logger.info("[TEST_BALANCE_CHECK] Testing wallet balance check endpoint")
        payment_service = PaymentService(db)

        # Call Orchard API using dedicated balance check endpoint
        http_response = payment_service.payment_gateway_client.check_wallet_balance()

        logger.info(f"[TEST_BALANCE_CHECK_RESPONSE] Status: {http_response.status_code}, Body: {http_response.text}")

        if http_response.status_code == 200:
            balance_data = http_response.json()
            return {
                "status": "success",
                "http_status": http_response.status_code,
                "data": balance_data
            }
        else:
            error_data = http_response.json()
            return {
                "status": "error",
                "http_status": http_response.status_code,
                "data": error_data
            }

    except Exception as e:
        logger.error(f"[TEST_BALANCE_CHECK_ERROR] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error checking balance: {str(e)}")

@payment_routes.post("/account-inquiry")
def account_inquiry(
    customer_number: str = Query(..., description="Customer phone number (e.g., 233200018204)"),
    network: str = Query(..., description="Network code (e.g., MTN, VOD, AIR, BNK)"),
    bank_code: str = Query(None, description="Bank code (required for BNK network)"),
    db: Session = Depends(get_db)
):
    """
    Account Information Inquiry (AII) endpoint.
    Verify account details before initiating transactions.

    Parameters:
    - customer_number: Phone number or account number (e.g., 233200018204)
    - network: Network/Bank code (MTN, VOD, AIR for mobile, BNK for bank)
    - bank_code: Bank code if network is BNK (e.g., VOD for a specific bank)

    Example:
    POST /api/v1/payment/account-inquiry?customer_number=233200018204&network=MTN
    """
    try:
        logger.info(f"[ACCOUNT_INQUIRY] Request for {customer_number} on {network}")
        payment_service = PaymentService(db)

        # Use PaymentGatewayClient.account_inquiry() method (DRY principle)
        http_response = payment_service.payment_gateway_client.account_inquiry(
            customer_number=customer_number,
            network=network,
            bank_code=bank_code
        )

        logger.info(f"[ACCOUNT_INQUIRY_RESPONSE] Status: {http_response.status_code}")

        if http_response.status_code == 200:
            account_data = http_response.json()
            return {
                "status": "success",
                "http_status": http_response.status_code,
                "customer_number": customer_number,
                "network": network,
                "data": account_data
            }
        else:
            error_data = http_response.json()
            return {
                "status": "error",
                "http_status": http_response.status_code,
                "customer_number": customer_number,
                "network": network,
                "data": error_data
            }

    except Exception as e:
        logger.error(f"[ACCOUNT_INQUIRY_ERROR] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during account inquiry: {str(e)}")


@payment_routes.post("/ctm")
def ctm_test_endpoint(
    customer_phone: str = Query(..., description="Customer phone number (e.g., 233550748724 or 0550748724)"),
    amount: float = Query(..., description="Amount to test (e.g., 5.0)"),
    reference: str = Query("CTM Test", description="Transaction reference/description"),
    db: Session = Depends(get_db)
):
    """
    CTM (Customer to Merchant) test endpoint - ONE-OFF TESTING ONLY.

    Sends a CTM request directly to Orchard API without creating any transaction records.
    This is purely for testing purposes and does NOT save anything to the database.

    Parameters:
    - customer_phone: Customer's phone number (233XXXXXXXXX or 0XXXXXXXXX format)
    - amount: Amount to test (e.g., 5.0)
    - reference: Transaction reference/description (optional, default: "CTM Test")

    Example:
    POST /api/v1/payment/ctm?customer_phone=233550748724&amount=5.0&reference=Test%20Payment

    WARNING: This endpoint only tests the API call and does NOT save the transaction.
    """
    try:
        logger.info(f"[CTM_TEST] Testing CTM request from {customer_phone}, Amount: GHS {amount}")

        from core.customers.utility.network_detector import NetworkDetector
        from utilities.uniqueidgenerator import UniqueIdGenerator

        # Detect network from customer phone
        detected_network, _ = NetworkDetector.detect_network_from_phone(customer_phone)

        network_map = {
            "MTN": "MTN",
            "VOD": "VOD",
            "AIR": "AIR",
        }

        selected_network = network_map.get(detected_network, "MTN") if detected_network else "MTN"

        # Build CTM request payload
        ctm_request = {
            "service_id": "4892",  # From environment config
            "trans_type": "CTM",
            "customer_number": customer_phone,
            "nw": selected_network,
            "amount": str(amount),
            "exttrid": str(UniqueIdGenerator.generate()),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reference": reference,
            "callback_url": "https://your-callback-url.com/callback"
        }

        logger.info(f"[CTM_TEST] Sending CTM request to Orchard API: {ctm_request}")

        # Send directly to Orchard API without saving to database
        payment_service = PaymentService(db)
        http_response = payment_service.payment_gateway_client.process_payment(ctm_request)

        logger.info(f"[CTM_TEST_RESPONSE] Status: {http_response.status_code}, Body: {http_response.text}")

        if http_response.status_code == 200:
            response_data = http_response.json()
            return {
                "status": "success",
                "message": "CTM test request sent successfully (NOT SAVED)",
                "http_status": http_response.status_code,
                "customer_phone": customer_phone,
                "amount": amount,
                "reference": reference,
                "network": selected_network,
                "api_response": response_data
            }
        else:
            error_data = http_response.json()
            return {
                "status": "error",
                "message": "CTM test request failed",
                "http_status": http_response.status_code,
                "customer_phone": customer_phone,
                "amount": amount,
                "reference": reference,
                "network": selected_network,
                "api_response": error_data
            }

    except Exception as e:
        logger.error(f"[CTM_TEST_ERROR] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during CTM test: {str(e)}")


@payment_routes.post("/ext-billers")
def external_billers_inquiry(
    customer_number: str = Query(..., description="Customer phone number (e.g., 020410181221)"),
    network: str = Query("ABS", description="Network code (default: ABS for external billers)"),
    operation: str = Query("INF", description="Operation type (default: INF for information inquiry)"),
    db: Session = Depends(get_db)
):
    """
    External Billers Inquiry (BLI) endpoint.
    Query available billers and bill information using the /extBillers endpoint.

    Parameters:
    - customer_number: Customer phone number or account number (e.g., 020410181221)
    - network: Network code (default: ABS for external billers)
    - operation: Operation type (default: INF for information/inquiry)

    Example:
    POST /api/v1/payment/ext-billers?customer_number=020410181221&network=ABS&operation=INF
    """
    try:
        logger.info(f"[EXT_BILLERS_INQUIRY] Request for {customer_number} on {network}, operation: {operation}")
        payment_service = PaymentService(db)

        # Use PaymentGatewayClient.external_billers_inquiry() method
        http_response = payment_service.payment_gateway_client.external_billers_inquiry(
            customer_number=customer_number,
            network=network,
            operation=operation
        )

        logger.info(f"[EXT_BILLERS_INQUIRY_RESPONSE] Status: {http_response.status_code}")

        if http_response.status_code == 200:
            billers_data = http_response.json()
            return {
                "status": "success",
                "http_status": http_response.status_code,
                "customer_number": customer_number,
                "network": network,
                "operation": operation,
                "data": billers_data
            }
        else:
            error_data = http_response.json()
            return {
                "status": "error",
                "http_status": http_response.status_code,
                "customer_number": customer_number,
                "network": network,
                "operation": operation,
                "data": error_data
            }

    except Exception as e:
        logger.error(f"[EXT_BILLERS_INQUIRY_ERROR] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during external billers inquiry: {str(e)}")


@payment_routes.post("/ext-biller-invoice")
def external_biller_invoice_inquiry(
    ext_biller_ref_id: str = Query(..., description="Biller ID from billers list (e.g., D9C37F3D52)"),
    ext_biller_pan: str = Query(..., description="Customer reference/ID for that biller (e.g., 20784533)"),
    ext_biller_ref_type: str = Query(..., description="Biller category/type (e.g., School Fees)"),
    network: str = Query("ABS", description="Network code (default: ABS for external billers)"),
    operation: str = Query("INV", description="Operation type (default: INV for invoice inquiry)"),
    db: Session = Depends(get_db)
):
    """
    External Biller Invoice Inquiry endpoint.
    Get customer invoice/bill information for a specific biller using the /extBillers endpoint with operation INV.

    Parameters:
    - ext_biller_ref_id: Biller ID from the billers list inquiry (e.g., D9C37F3D52)
    - ext_biller_pan: Customer reference/ID number for that biller (e.g., 20784533)
    - ext_biller_ref_type: Biller category/type (e.g., School Fees)
    - network: Network code (default: ABS for external billers)
    - operation: Operation type (default: INV for invoice inquiry)

    Example:
    POST /api/v1/payment/ext-biller-invoice?ext_biller_ref_id=D9C37F3D52&ext_biller_pan=20784533&ext_biller_ref_type=School%20Fees
    """
    try:
        logger.info(f"[EXT_BILLER_INVOICE_INQUIRY] Request for biller_ref_id={ext_biller_ref_id}, pan={ext_biller_pan}, type={ext_biller_ref_type}")
        payment_service = PaymentService(db)

        # Use PaymentGatewayClient.external_biller_invoice_inquiry() method
        http_response = payment_service.payment_gateway_client.external_biller_invoice_inquiry(
            ext_biller_ref_id=ext_biller_ref_id,
            ext_biller_pan=ext_biller_pan,
            ext_biller_ref_type=ext_biller_ref_type,
            network=network,
            operation=operation
        )

        logger.info(f"[EXT_BILLER_INVOICE_INQUIRY_RESPONSE] Status: {http_response.status_code}")

        if http_response.status_code == 200:
            invoice_data = http_response.json()
            return {
                "status": "success",
                "http_status": http_response.status_code,
                "ext_biller_ref_id": ext_biller_ref_id,
                "ext_biller_pan": ext_biller_pan,
                "ext_biller_ref_type": ext_biller_ref_type,
                "network": network,
                "operation": operation,
                "data": invoice_data
            }
        else:
            error_data = http_response.json()
            return {
                "status": "error",
                "http_status": http_response.status_code,
                "ext_biller_ref_id": ext_biller_ref_id,
                "ext_biller_pan": ext_biller_pan,
                "ext_biller_ref_type": ext_biller_ref_type,
                "network": network,
                "operation": operation,
                "data": error_data
            }

    except Exception as e:
        logger.error(f"[EXT_BILLER_INVOICE_INQUIRY_ERROR] Error: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error during external biller invoice inquiry: {str(e)}")
