"""Formatting utilities for the dashboard."""


def format_duration(total_seconds):
    """Convert total seconds to a human-readable duration format (e.g., '1h 2m 5s').
    
    Args:
        total_seconds: Total duration in seconds
    
    Returns:
        Formatted string like "1h 2m 5s"
        
    Bug fix: Previously, this function returned incorrect minutes for any duration 
    over an hour (e.g., 3725 seconds would display "1h 62m" instead of "1h 2m").
    The fix uses the remainder after extracting hours: (total_seconds % 3600) // 60
    """
    if total_seconds < 0:
        return "0s"
    
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:  # Show seconds if nothing else, or if > 0
        parts.append(f"{seconds}s")
    
    return " ".join(parts)
