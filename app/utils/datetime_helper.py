from datetime import datetime, timezone
from typing import Optional, Dict, Any


def format_datetime_response(dt: datetime) -> Dict[str, Any]:
    """
    Format datetime for API responses with timezone information.
    
    Returns a dictionary with:
    - iso: ISO 8601 string with timezone
    - timestamp: Unix timestamp in seconds
    - timezone: UTC offset in hours
    """
    if not dt:
        return None
        
    # Ensure datetime is timezone-aware
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        
    return {
        "iso": dt.isoformat(),
        "timestamp": dt.timestamp(),
        "timezone": "+00:00"  # Always UTC
    }


def convert_datetime_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert all datetime fields in a dictionary to formatted datetime responses.
    This helps frontend to properly handle timezone conversions.
    """
    if not data:
        return data
        
    result = data.copy()
    
    for key, value in data.items():
        if isinstance(value, datetime):
            result[key] = format_datetime_response(value)
        elif isinstance(value, dict):
            result[key] = convert_datetime_fields(value)
        elif isinstance(value, list):
            result[key] = [
                convert_datetime_fields(item) if isinstance(item, dict) else item 
                for item in value
            ]
            
    return result
