from PIL import Image, ImageDraw, ImageFont
import io
import base64
from datetime import datetime
from typing import Dict, Any
import os
import logging

logger = logging.getLogger(__name__)

class ReceiptGenerator:
    def __init__(self):
        self.assets_dir = os.path.join(os.path.dirname(__file__), "assets")
        
        # Ensure assets directory exists
        if not os.path.exists(self.assets_dir):
            os.makedirs(self.assets_dir)
            logger.warning(f"Created assets directory at: {self.assets_dir}")

        self.logo = self._load_icon("viin_logo.png")
        self.success_icon = self._load_icon("success_icon.png")
        self.failed_icon = self._load_icon("failed_icon.png")

    def _load_icon(self, icon_filename: str):
        """Load an icon with proper error handling"""
        try:
            icon_path = os.path.join(self.assets_dir, icon_filename)
            if os.path.exists(icon_path):
                icon = Image.open(icon_path).convert("RGBA")
                logger.info(f"Loaded icon: {icon_filename}")
                return icon
            else:
                logger.warning(f"Icon not found: {icon_path}")
                return None
        except Exception as e:
            logger.warning(f"Could not load icon {icon_filename}: {str(e)}")
            return None

    def generate_receipt_image(self, data: Dict[str, Any]) -> str:
        """Generate receipt image and return as base64 data URL"""
        # Check if this is a loan receipt based on the presence of loan fields
        loan_fields = ['interest_rate', 'loan_period', 'expected_pay_date', 'penalty_rate']
        has_loan_fields = any(field in data for field in loan_fields)
        
        if has_loan_fields:
            image = self._generate_loan_receipt(data)
        else:
            image = self._generate_receipt(data)

        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        encoded = base64.b64encode(buffered.getvalue()).decode()

        return f"data:image/png;base64,{encoded}"

    # -----------------------------
    #   UI UTILS
    # -----------------------------
    def _font(self, name="VastagoGrotesk-Regular.otf", size=18):
        """Get Vastago Grotesk font with proper fallbacks"""
        try:
            font_path = os.path.join(self.assets_dir, name)
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, size)
            else:
                logger.warning(f"Vastago font not found: {font_path}")
        except Exception as e:
            logger.warning(f"Error loading Vastago font {name}: {str(e)}")
        
        # Fallbacks
        try:
            return ImageFont.truetype("arial.ttf", size)
        except:
            try:
                return ImageFont.truetype("DejaVuSans.ttf", size)
            except:
                logger.warning("Using default font")
                return ImageFont.load_default()

    # -----------------------------
    #   NEW RECEIPT LAYOUT
    # -----------------------------
    def _generate_receipt(self, data: Dict[str, Any]) -> Image.Image:
        WIDTH, HEIGHT = 1080, 1600

        img = Image.new("RGB", (WIDTH, HEIGHT), "#EDEDED")
        draw = ImageDraw.Draw(img)

        # Colors
        green = "#009B51"
        dark = "#111111"
        label = "#666666"
        card_bg = "#E6E6E4"
        divider = "#92938E"

        # Fonts
        # Fonts - Using Vastago Grotesk
        title_fnt = self._font("VastagoGrotesk-Bold.otf", 50)
        header_fnt = self._font("VastagoGrotesk-Bold.otf", 34)
        bold_fnt = self._font("VastagoGrotesk-Bold.otf", 30)
        regular_fnt = self._font(size=30)
        small_fnt = self._font(size=24)

        # -----------------------------------------
        #   TOP BAR
        # -----------------------------------------
        y = 80

        if self.logo:
            try:
                logo_resized = self.logo.resize((200, 66))
                # Create a new RGBA image for the logo to preserve transparency
                logo_bg = Image.new("RGBA", logo_resized.size, (0, 0, 0, 0))
                logo_bg.paste(logo_resized, (0, 0), logo_resized)
                img.paste(logo_bg, (80, y), logo_bg)
            except Exception as e:
                logger.warning(f"Error pasting logo: {str(e)}")
                draw.text((80, y), "Viin", font=bold_fnt, fill=dark)

        draw.text((WIDTH - 80, y + 20),
                  "Transaction Receipt",
                  anchor="ra",
                  font=regular_fnt,
                  fill=label)

        # -----------------------------------------
        #   STATUS ICON + TEXT
        # -----------------------------------------
        y += 140
        cx = WIDTH // 2

        icon_bg_color = "#D1D2CD"
        is_failed = data.get("status", "").lower() == "failed"
        if is_failed:
            icon_bg_color = "#FDE4E4"

        # Circle background - scaled down to match 60x60 icon
        draw.ellipse([
            cx - 50, y - 50,    # Radius 50 (was 70)
            cx + 50, y + 50
        ], fill=icon_bg_color)

        icon = self.failed_icon if is_failed else self.success_icon
        if icon:
            try:
                icon_resized = icon.resize((60, 60))  # Scaled down from 90x90
                # Ensure the icon has alpha channel
                if icon_resized.mode != 'RGBA':
                    icon_resized = icon_resized.convert('RGBA')
                
                # Create a new image for the icon to preserve transparency
                icon_bg = Image.new("RGBA", icon_resized.size, (0, 0, 0, 0))
                icon_bg.paste(icon_resized, (0, 0), icon_resized)
                img.paste(icon_bg, (cx - 30, y - 30), icon_bg)  # Adjusted position
            except Exception as e:
                logger.warning(f"Error pasting status icon: {str(e)}")
                # Draw fallback icon - also scaled down
                if is_failed:
                    draw.line([(cx-20, y-20), (cx+20, y+20)], fill="#C62828", width=6)
                    draw.line([(cx+20, y-20), (cx-20, y+20)], fill="#C62828", width=6)
                else:
                    draw.line([(cx-15, y), (cx, y+15), (cx+20, y-7)], fill=green, width=6)

        # Status message
        y += 150  # Reduced spacing since icon is smaller
        msg = "Your money transfer was\nsuccessful!" if not is_failed else "Your money transfer\nfailed!"
        status_color = green if not is_failed else "#C62828"

        # For multiline text with center alignment:
        lines = msg.split('\n')
        line_height = title_fnt.getbbox("Ay")[3]  # Get approximate line height

        # Calculate starting y position for multiline text
        text_y = y - (len(lines) - 1) * line_height // 2

        # Draw each line centered
        for i, line in enumerate(lines):
            draw.text((cx, text_y + i * line_height), 
                    line, 
                    fill=status_color, 
                    font=title_fnt, 
                    anchor="mm")

        # -----------------------------------------
        #   MAIN CARD
        # -----------------------------------------
        card_top = y + 110
        card_radius = 50

        draw.rounded_rectangle(
            [60, card_top, WIDTH - 80, HEIGHT - 125],
            radius=40,
            fill="#D7D8D3"
        )

        inner_left = 140
        inner_right = WIDTH - 140
        y = card_top + 45

        # -----------------------------------------
        #   SECTION: TRANSACTION DETAILS
        # -----------------------------------------
        draw.text((inner_left, y), "Transaction Details", font=header_fnt, fill=green)
        y += 50

        self._row(draw, inner_left, inner_right, y, "Amount", f"GHC {data.get('amount', '0.00')}", regular_fnt, bold_fnt)
        y += 65

        self._row(draw, inner_left, inner_right, y, "Transaction ID", data.get("transaction_id", "N/A"), regular_fnt, regular_fnt)
        y += 75

        draw.line([(inner_left, y), (inner_right, y)], fill=divider, width=1)
        y += 60

        # -----------------------------------------
        #   SENDER ACCOUNT
        # -----------------------------------------
        draw.text((inner_left, y), "Sender Account", font=header_fnt, fill=green)
        y += 70

        self._row(draw, inner_left, inner_right, y, "Account Name", data.get("sender_name", "N/A"), regular_fnt, bold_fnt)
        y += 65
        self._row(draw, inner_left, inner_right, y, "Account Number", data.get("sender_account", "N/A"), regular_fnt, regular_fnt)
        y += 65
        self._row(draw, inner_left, inner_right, y, "Provider", data.get("sender_provider", "N/A"), regular_fnt, regular_fnt)
        y += 75

        draw.line([(inner_left, y), (inner_right, y)], fill=divider, width=1)
        y += 60

        # -----------------------------------------
        #   RECIPIENT ACCOUNT
        # -----------------------------------------
        draw.text((inner_left, y), "Recipient Account", font=header_fnt, fill=green)
        y += 70

        self._row(draw, inner_left, inner_right, y, "Account Name", data.get("receiver_name", "N/A"), regular_fnt, bold_fnt)
        y += 65
        self._row(draw, inner_left, inner_right, y, "Account Number", data.get("receiver_account", "N/A"), regular_fnt, regular_fnt)
        y += 65
        self._row(draw, inner_left, inner_right, y, "Provider", data.get("receiver_provider", "N/A"), regular_fnt, regular_fnt)
        y += 90

        # Footer date / time
        timestamp = data.get("timestamp", datetime.now())
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except:
                timestamp = datetime.now()
        
        date_str = timestamp.strftime("%a. %b %d, %Y")
        time_str = timestamp.strftime("%H:%M:%S")

        draw.text((inner_left, y), date_str, font=small_fnt, fill=dark)
        draw.text((inner_right, y), time_str, font=small_fnt, fill=dark, anchor="ra")

        # -----------------------------------------
        #   BOTTOM FOOTER TEXT
        # -----------------------------------------
        draw.text((WIDTH // 2, HEIGHT - 80),
                  "This payment was made with Viin",
                  anchor="mm",
                  fill=label,
                  font=small_fnt)

        return img

    # Utility for left/right rows
    def _row(self, draw, left, right, y, label, value, label_font, value_font):
        draw.text((left, y), label, font=label_font, fill="#666666")
        draw.text((right, y), str(value), font=value_font, fill="#111111", anchor="ra")

    def _generate_loan_receipt(self, data: Dict[str, Any]) -> Image.Image:
        """Generate loan receipt with loan-specific styled layout"""
        width, height = 1080, 1600  # Use same dimensions as regular receipt
        img = Image.new("RGB", (width, height), "#EDEDED")
        draw = ImageDraw.Draw(img)

        # Colors
        primary_color = "#1a237e"
        is_failed = data.get('status', '').lower() == 'failed'
        if is_failed:
            accent_color = "#FFEBEE"
            icon_color = "#d32f2f"
        else:
            accent_color = "#E6F4EA"
            icon_color = "#34A853"
            
        gray_text = "#666666"
        black_text = "#222222"
        light_gray_bg = "#F6F8FA"
        border_color = "#E0E0E0"
        danger_color = "#d32f2f"

        # Fonts - adjust sizes for larger canvas
        title_font = self._font("VastagoGrotesk-Bold.otf", 52)
        subtitle_font = self._font("VastagoGrotesk-Regular.otf", 38)
        bold_font = self._font("VastagoGrotesk-SemiBold.otf", 40)
        regular_font = self._font("VastagoGrotesk-Regular.otf", 38)
        small_font = self._font("VastagoGrotesk-Regular.otf", 32)
        # -----------------------------------------
        #   TOP BAR (same as regular receipt)
        # -----------------------------------------
        y = 80

        if self.logo:
            try:
                logo_resized = self.logo.resize((200, 60))
                logo_bg = Image.new("RGBA", logo_resized.size, (0, 0, 0, 0))
                logo_bg.paste(logo_resized, (0, 0), logo_resized)
                img.paste(logo_bg, (80, y), logo_bg)
            except:
                draw.text((80, y), "Viin", font=bold_font, fill=black_text)

        draw.text((width - 80, y + 20),
                  "Loan Receipt",
                  anchor="ra",
                  font=regular_font,
                  fill=gray_text)

        # -----------------------------------------
        #   STATUS ICON + TEXT
        # -----------------------------------------
        y += 140
        cx = width // 2

        # Circle background
        draw.ellipse([
            cx - 70, y - 70,
            cx + 70, y + 70
        ], fill=accent_color)

        icon = self.failed_icon if is_failed else self.success_icon
        if icon:
            try:
                icon_resized = icon.resize((90, 90))
                if icon_resized.mode != 'RGBA':
                    icon_resized = icon_resized.convert('RGBA')
                
                icon_bg = Image.new("RGBA", icon_resized.size, (0, 0, 0, 0))
                icon_bg.paste(icon_resized, (0, 0), icon_resized)
                img.paste(icon_bg, (cx - 45, y - 45), icon_bg)
            except:
                # Draw fallback icon
                if is_failed:
                    draw.line([(cx-30, y-30), (cx+30, y+30)], fill="#C62828", width=8)
                    draw.line([(cx+30, y-30), (cx-30, y+30)], fill="#C62828", width=8)
                else:
                    draw.line([(cx-20, y), (cx, y+20), (cx+30, y-10)], fill=icon_color, width=8)

        # Status message
        y += 150
        status_text = "Failed!" if is_failed else "Successful!"
        draw.text((cx, y), "Loan Disbursement", font=title_font, fill=black_text, anchor="mm")
        y += 70
        draw.text((cx, y), status_text, font=title_font, fill=icon_color, anchor="mm")

        # Subtext
        y += 50
        if is_failed:
            subtext = "Your loan disbursement failed."
        else:
            subtext = "Your loan has been disbursed to the receiver."
        draw.text((cx, y), subtext, font=subtitle_font, fill=gray_text, anchor="mm")

        # -----------------------------------------
        #   MAIN CARD
        # -----------------------------------------
        card_top = y + 80

        draw.rounded_rectangle(
            [80, card_top, width - 80, height - 180],
            radius=40,
            fill="white"
        )

        inner_left = 140
        inner_right = width - 140
        y = card_top + 80

        # -----------------------------------------
        #   SECTION: TRANSACTION DETAILS
        # -----------------------------------------
        draw.text((inner_left, y), "Transaction Details", font=bold_font, fill=black_text)
        y += 70

        # Amount with special color
        draw.text((inner_left, y), "Amount", font=regular_font, fill=gray_text)
        amount_text = f"GHS {data.get('amount', '0.00')}"
        draw.text((inner_right, y), amount_text, font=bold_font, fill=icon_color, anchor="ra")
        y += 65

        self._row(draw, inner_left, inner_right, y, "Transaction ID", data.get("transaction_id", "N/A"), regular_font, bold_font)
        y += 75

        # Status tag
        draw.text((inner_left, y), "Status", font=regular_font, fill=gray_text)
        status_value = data.get('status', 'Success')
        tag_w = 120
        tag_h = 40
        tx = inner_right - tag_w
        ty = y - 10
        tag_bg = "#FFEBEE" if is_failed else "#E6F4EA"
        tag_fg = "#d32f2f" if is_failed else "#34A853"
        draw.rounded_rectangle([tx, ty, tx + tag_w, ty + tag_h], radius=20, fill=tag_bg)
        draw.text((tx + tag_w / 2, ty + tag_h / 2), status_value, font=small_font, fill=tag_fg, anchor="mm")
        y += 85

        draw.line([(inner_left, y), (inner_right, y)], fill=border_color, width=3)
        y += 60

        # -----------------------------------------
        #   LOAN DETAILS SECTION
        # -----------------------------------------
        draw.text((inner_left, y), "Loan Details", font=bold_font, fill=primary_color)
        y += 70

        # Loan-specific fields
        loan_fields = [
            ("Interest Rate", f"{data.get('interest_rate', '0')}%"),
            ("Loan Period", data.get('loan_period', 'N/A')),
            ("Expected Pay Date", data.get('expected_pay_date', 'N/A')),
            ("Penalty Rate", f"{data.get('penalty_rate', '0')}%"),
        ]

        for label, value in loan_fields:
            draw.text((inner_left, y), label, font=regular_font, fill=gray_text)
            if label in ["Interest Rate", "Penalty Rate"]:
                draw.text((inner_right, y), value, font=bold_font, fill=danger_color, anchor="ra")
            else:
                draw.text((inner_right, y), value, font=bold_font, fill=black_text, anchor="ra")
            y += 65

        # Footer date / time
        timestamp = data.get("timestamp", datetime.now())
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            except:
                timestamp = datetime.now()
        
        date_str = timestamp.strftime("%a. %b %d, %Y")
        time_str = timestamp.strftime("%H:%M:%S")

        draw.text((inner_left, y), date_str, font=small_font, fill=black_text)
        draw.text((inner_right, y), time_str, font=small_font, fill=black_text, anchor="ra")

        # -----------------------------------------
        #   BOTTOM FOOTER TEXT
        # -----------------------------------------
        draw.text((width // 2, height - 80),
                  "Manage your loan in the Viin app!",
                  anchor="mm",
                  fill=gray_text,
                  font=small_font)

        return img