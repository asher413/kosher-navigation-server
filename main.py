import time
import logging
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Depends, Security
from fastapi.security.api_key import APIKeyHeader
import httpx
from sqlalchemy import create_engine, Column, String, Integer, Float
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# --- הגדרות בסיסיות ולוגים ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = "Your-Super-Secret-Key-123" # תשנה את זה למפתח אמיתי
API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=True)

COOLDOWN_SECONDS = 3600 # שעה המתנה לפני שנשלח שוב לאותו מספר
SMS_API_URL = "https://api.your-sms-provider.com/send"

# --- הגדרת מסד נתונים (SQLite) ---
# שימוש ב-DB מאפשר שמירת נתונים גם אם השרת עושה הפעלה מחדש
SQLALCHEMY_DATABASE_URL = "sqlite:///./leads_data.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class CallLog(Base):
    __tablename__ = "call_logs"
    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    last_called_at = Column(Float)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Advanced Lead Auto-Responder")

# --- פונקציות עזר ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def verify_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header != API_KEY:
        raise HTTPException(status_code=403, detail="Could not validate API KEY")
    return api_key_header

# --- לוגיקת הליבה: שליחת הודעה ברקע ---
async def process_and_send_sms(phone: str, message: str, is_local_dev: bool = False):
    """
    שליחה אסינכרונית חכמה עם טיפול ב-Timeouts ושגיאות תעודה.
    is_local_dev: מומלץ להעביר ל-True רק כשאתה מפתח מקומית מאחורי סינון אינטרנט.
    """
    # הגדרת Timeout קשיח כדי למנוע תקיעות מול ה-API של ההודעות
    timeout_settings = httpx.Timeout(15.0, connect=5.0)
    
    # במידה ויש שגיאות SSL (כמו שקורה הרבה בסינוני אינטרנט מסוימים), 
    # אפשר לבטל אימות תעודה בסביבת פיתוח בלבד: verify=False
    verify_cert = False if is_local_dev else True 

    async with httpx.AsyncClient(timeout=timeout_settings, verify=verify_cert) as client:
        try:
            logger.info(f"Attempting to send message to {phone}...")
            # תבנית JSON לדוגמה. יש להתאים לספק ה-SMS שלך
            payload = {"destination": phone, "text": message}
            response = await client.post(SMS_API_URL, json=payload)
            response.raise_for_status()
            logger.info(f"✅ Successfully sent to {phone}")
            
        except httpx.ConnectTimeout:
            logger.error(f"❌ Connection Timeout while connecting to SMS provider for {phone}")
        except httpx.ConnectError as e:
            logger.error(f"❌ SSL/Connection error for {phone}: {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error sending to {phone}: {str(e)}")

# --- נקודת הקצה (Webhook) ---
@app.get("/api/v1/webhook/missed-call", dependencies=[Depends(verify_api_key)])
async def handle_missed_call(
    background_tasks: BackgroundTasks,
    ApiPhone: str = None, # מותאם לפרמטרים של מערכות כמו ימות המשיח
    caller_id: str = None, # תמיכה במערכות אחרות
    db: Session = Depends(get_db)
):
    """
    Webhook לקבלת פניות. 
    תומך בבקשות GET עם פרמטרים ב-URL (כמו במערכות IVR רבות).
    """
    phone = ApiPhone or caller_id
    if not phone:
        raise HTTPException(status_code=400, detail="Missing phone number parameter")

    current_time = time.time()
    
    # בדיקה במסד הנתונים האם הלקוח התקשר לאחרונה
    log_entry = db.query(CallLog).filter(CallLog.phone_number == phone).first()

    if log_entry:
        if (current_time - log_entry.last_called_at) < COOLDOWN_SECONDS:
            logger.info(f"Skipping {phone} - Cooldown active.")
            return {"status": "ignored", "reason": "rate_limit_active"}
        
        # עדכון זמן אחרון
        log_entry.last_called_at = current_time
    else:
        # לקוח חדש לחלוטין
        new_entry = CallLog(phone_number=phone, last_called_at=current_time)
        db.add(new_entry)
        
    db.commit()

    message = "היי! ראינו שחיפשת אותנו. אנחנו קצת עמוסים כרגע, אפשר לעזור בוואטסאפ? נשמח לתת שירות! 🛠️"
    
    # הוספת המשימה לרקע כדי להחזיר מיד 200 OK למרכזייה (מונע ניתוק שיחה מהצד שלהם)
    background_tasks.add_task(process_and_send_sms, phone, message, is_local_dev=False)

    return {"status": "success", "message": "Queued for sending"}
