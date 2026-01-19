# Import OS module for environment variable access
import os

# Load environment variables from a .env file
from dotenv import load_dotenv

# MongoDB client and error classes
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# Typing helper for optional return values
from typing import Optional, List, Dict, Any
import logging

# Import datetime for timestamps
from datetime import datetime


# ---------- Load .env file and initialize MongoDB URI ----------

# Load environment variables from the .env file into the environment
load_dotenv()

# Retrieve MongoDB URI from environment variables
MONGO_URI = os.getenv("MONGO_URI")

# ---------- MongoDB Connection Setup ----------

try:
    # Initialize MongoDB client with the URI
    if not MONGO_URI:
        raise ValueError("MONGO_URI environment variable not set.")
    
    # Disable MongoDB logs by setting logging level
    import logging
    logging.getLogger("pymongo").setLevel(logging.WARNING)
    logging.getLogger("pymongo.connection").setLevel(logging.WARNING)
    logging.getLogger("pymongo.pool").setLevel(logging.WARNING)
    logging.getLogger("pymongo.serverSelection").setLevel(logging.WARNING)
    
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)

    # Access the Lion Edge database/collection
    db = client["lion_edge_consultancy"]
    inquiries_collection = db["inquiries"]
    
    # Store client reference for connection testing
    _mongo_client = client
    _mongo_db = db

except (PyMongoError, ValueError) as e:
    # Re-raise, but also log for visibility
    logging.getLogger("realtime_realestate_agent").error(f"Mongo init failed: {e}")
    raise

# ---------- Inquiry Database Driver Class ----------

class DatabaseDriver:
    def __init__(self):
        # Initialize the collection reference to use in other methods
        self.collection = inquiries_collection
        self.log = logging.getLogger("realtime_realestate_agent")
        self._indexes_created = False
        
        # Test database connection on initialization
        try:
            # Ping the database to verify connection
            _mongo_client.admin.command('ping')
            self.log.info("✅ Database connection verified successfully")
        except Exception as e:
            self.log.warning(f"⚠️ Database connection test failed: {e}")
            # Don't raise - connection might still work, just log the warning
        
        # Don't create indexes here - do it lazily on first use to avoid blocking
    
    def _ensure_indexes(self):
        """Create indexes lazily (only once, non-blocking)"""
        if not self._indexes_created:
            try:
                # Create indexes in background (non-blocking)
                # Use sparse indexes to handle missing fields gracefully
                self.collection.create_index("phone", background=True, sparse=True)
                self.collection.create_index("created_at", background=True, sparse=True)
                self.collection.create_index("inquiry_type", background=True, sparse=True)
                self.collection.create_index("status", background=True, sparse=True)
                self._indexes_created = True
                self.log.info("✅ Database indexes created successfully")
            except Exception as e:
                # Log but don't fail - indexes are optional optimization
                self.log.warning(f"⚠️ Index creation failed (non-critical): {e}")
                self._indexes_created = True  # Mark as created to avoid retrying

    def _sanitize_value(self, value: Any) -> Any:
        """Convert values to MongoDB-compatible types"""
        if value is None:
            return None
        elif isinstance(value, (str, int, float, bool)):
            return value
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, dict):
            return {str(k): self._sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, (list, tuple)):
            return [self._sanitize_value(item) for item in value]
        elif hasattr(value, "model_dump"):
            # Pydantic model
            return self._sanitize_value(value.model_dump())
        elif hasattr(value, "dict"):
            # Pydantic model (older versions)
            return self._sanitize_value(value.dict())
        else:
            # Convert to string as fallback
            return str(value)

    def _sanitize_inquiry_data(self, inquiry_data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize inquiry_data to ensure it's MongoDB-compatible"""
        if not inquiry_data:
            return {}
        
        try:
            # Recursively sanitize all values
            sanitized = {}
            for key, value in inquiry_data.items():
                # Convert key to string
                str_key = str(key) if key else "unknown_key"
                # Sanitize value
                sanitized[str_key] = self._sanitize_value(value)
            return sanitized
        except Exception as e:
            self.log.warning(f"Error sanitizing inquiry_data: {e}, using string conversion")
            return {"raw_data": str(inquiry_data)}

    # Create a new inquiry in the MongoDB collection
    def create_inquiry(self, phone: str, inquiry_type: str, inquiry_data: Dict[str, Any], name: str = None, caller_phone: str = None) -> Optional[dict]:
        # Log that DB connection is being triggered
        self.log.info("🔌 Database connection triggered by create_inquiry")
        
        # Ensure indexes exist (lazy, non-blocking)
        self._ensure_indexes()
        
        try:
            # Sanitize and validate phone
            if not phone or phone == "unknown" or not isinstance(phone, str):
                import time
                phone = f"call_{int(time.time())}"
                self.log.info(f"Database: Using fallback phone: {phone}")
            
            # Ensure phone is a string
            phone = str(phone).strip()
            if not phone:
                import time
                phone = f"call_{int(time.time())}"
            
            # Sanitize inquiry_type
            inquiry_type = str(inquiry_type).strip() if inquiry_type else "general"
            
            # Sanitize inquiry_data
            sanitized_inquiry_data = self._sanitize_inquiry_data(inquiry_data) if inquiry_data else {}
            
            # Build inquiry document with all fields properly sanitized
            inquiry = {
                "phone": phone,
                "inquiry_type": inquiry_type,
                "inquiry_data": sanitized_inquiry_data,
                "status": "new",
                "created_at": datetime.utcnow().isoformat() + "Z",  # Use UTC and ISO format
                "source": "phone_call",
                "phone_source": "provided_by_customer"
            }
            
            # Add optional fields if provided (sanitized)
            if name:
                inquiry["name"] = str(name).strip() if name else None
            
            # Add caller phone number if available
            if caller_phone and caller_phone != "unknown" and caller_phone != "extracted_failed":
                inquiry["caller_phone"] = str(caller_phone).strip()
                inquiry["phone_source"] = "extracted_from_call"
            
            # Log before insertion
            self.log.info(f"Database: Inserting inquiry with phone: {inquiry.get('phone')}")
            self.log.info(f"Database: Inquiry type: {inquiry.get('inquiry_type')}")
            self.log.info(f"Database: Inquiry data keys: {list(sanitized_inquiry_data.keys()) if sanitized_inquiry_data else 'empty'}")
            
            # Insert the inquiry document into the MongoDB collection
            result = self.collection.insert_one(inquiry)
            self.log.info(f"Database: Insert successful, ID: {result.inserted_id}")
            
            # Return the inserted document with ID
            inquiry["_id"] = str(result.inserted_id)
            return inquiry
            
        except Exception as e:
            # Catch ALL exceptions, not just PyMongoError
            self.log.error(f"Database: Insert failed with error: {e}")
            self.log.error(f"Database: Error type: {type(e).__name__}")
            import traceback
            self.log.error(f"Database: Traceback: {traceback.format_exc()}")
            return None
    
    # Retrieve an inquiry document by phone number
    def get_inquiry_by_phone(self, phone: str) -> Optional[dict]:
        try:
            # Search for an inquiry with the matching phone number, get the most recent one
            inquiry = self.collection.find_one({"phone": phone}, sort=[("_id", -1)])
            return inquiry
        except PyMongoError:
            return None
